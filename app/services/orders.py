"""Order creation and lifecycle (idempotent, transactional)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import (
    Cart,
    CouponCode,
    Fulfillment,
    FulfillmentStatus,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    ProductVariant,
    User,
)
from app.security import new_idempotency_key
from app.services.notifications import notify_user
from app.services.pricing import (
    compute_discount,
    compute_price,
    is_margin_below_minimum,
)

log = get_logger("vyron.orders")


class CheckoutError(Exception):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(key)
        self.key = key
        self.detail = detail


class QuickLine:
    """Transient cart line for one-click "buy now" checkout."""

    def __init__(self, variant: ProductVariant, quantity: int, player_info: dict):
        self.variant = variant
        self.quantity = quantity
        self.player_info = player_info


class QuickCart:
    def __init__(self, lines: list[QuickLine]):
        self.items = lines
        self.coupon_code = None


def next_order_number(session: Session) -> str:
    """VYR-YYYYMMDD-000001 with a transactional daily counter."""
    day = datetime.utcnow().strftime("%Y%m%d")
    name = f"order:{day}"
    from app.models import Counter

    counter = session.execute(
        select(Counter).where(Counter.name == name).with_for_update()
    ).scalar_one_or_none()
    if counter is None:
        counter = Counter(name=name, value=0)
        session.add(counter)
        session.flush()
    counter.value += 1
    return f"VYR-{day}-{counter.value:06d}"


def validate_player_info(product: Product, player_info: dict) -> Optional[str]:
    """Validate the required player fields for a product server-side."""
    fields = (product.required_fields or {}).get("fields", [])
    for field in fields:
        key = field.get("key")
        label = field.get("type", "string")
        required = field.get("required", True)
        value = (player_info or {}).get(key)
        if required and (value is None or str(value).strip() == ""):
            return f"missing_field:{key}"
        if value is not None and str(value).strip() != "":
            value_str = str(value).strip()
            if label == "number" and not value_str.replace("-", "").isdigit():
                return f"invalid_field:{key}"
            if label == "alphanum" and not value_str.isalnum():
                return f"invalid_field:{key}"
            if len(value_str) > 64:
                return f"invalid_field:{key}"
    return None


def create_order_from_cart(
    session: Session,
    user: User,
    cart: Cart,
    *,
    player_info: dict,
    coupon_code: Optional[str],
    idempotency_key: Optional[str] = None,
) -> Order:
    """Create an order with server-side price verification.

    Frontend prices are NEVER trusted. Every line is re-priced from the
    database; a variant without a real supplier cost cannot be sold.
    """
    idem = idempotency_key or new_idempotency_key()

    existing = session.execute(
        select(Order).where(Order.idempotency_key == idem)
    ).scalar_one_or_none()
    if existing is not None:
        return existing  # idempotent replay (even if cart was already consumed)

    if not cart.items:
        raise CheckoutError("cart_empty")

    subtotal = 0
    total_supplier_cost = 0
    total_fee = 0
    total_profit = 0
    line_data: list[dict] = []

    for item in cart.items:
        variant: ProductVariant = item.variant
        product: Optional[Product] = variant.product
        if product is None or not product.active or product.visibility is False:
            raise CheckoutError("product_unavailable")
        if not variant.active or not variant.in_stock or variant.supplier_cost is None:
            raise CheckoutError("variant_unavailable")
        err = validate_player_info(product, item.player_info or player_info)
        if err:
            raise CheckoutError(err)
        breakdown = compute_price(session, variant)
        qty = max(1, min(int(item.quantity or 1), 50))
        line_total = breakdown.unit_price * qty
        subtotal += line_total
        total_supplier_cost += breakdown.supplier_cost * qty
        total_fee += breakdown.fee * qty
        total_profit += breakdown.profit * qty
        line_data.append(
            {
                "item": item,
                "variant": variant,
                "product": product,
                "breakdown": breakdown,
                "qty": qty,
                "line_total": line_total,
            }
        )

    discount = 0
    if coupon_code:
        coupon = session.execute(
            select(CouponCode).where(CouponCode.code == coupon_code.upper().strip())
        ).scalar_one_or_none()
        if coupon is None:
            raise CheckoutError("coupon_invalid")
        used_by_user = session.execute(
            select(func.count(Order.id)).where(
                Order.user_id == user.id,
                Order.coupon_code == coupon.code,
                Order.status.notin_([OrderStatus.CANCELLED, OrderStatus.FAILED]),
            )
        ).scalar() or 0
        discount, err = compute_discount(session, coupon, subtotal, user.id, used_by_user)
        if err:
            raise CheckoutError(err)
        if not coupon.allow_below_margin:
            # discounts may never eat below the configured minimum margin floor
            from app.services.pricing import get_pricing_settings

            pricing = get_pricing_settings(session)
            min_percent = pricing.get("min_margin_percent", 0)
            min_floor = round(total_supplier_cost * min_percent / 100) if min_percent else 0
            if (total_profit - discount) < min_floor:
                raise CheckoutError("coupon_below_margin")

    total = max(0, subtotal - discount)

    order = Order(
        order_number=next_order_number(session),
        idempotency_key=idem,
        user_id=user.id,
        status=OrderStatus.PENDING_PAYMENT,
        subtotal_amount=subtotal,
        discount_amount=discount,
        fee_amount=total_fee,
        total_amount=total,
        profit_amount=total_profit - discount,
        currency="UZS",
        coupon_code=coupon_code.upper().strip() if coupon_code else None,
        player_info=_mask_player_info(player_info),
    )
    session.add(order)
    session.flush()

    for data in line_data:
        item: CartItemT = data["item"]
        variant = data["variant"]
        product = data["product"]
        breakdown = data["breakdown"]
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id,
                product_name=product.name,
                variant_name=variant.name,
                game_name=product.game.name if product.game else None,
                quantity=data["qty"],
                unit_price_amount=breakdown.unit_price,
                line_total_amount=data["line_total"],
                supplier_cost_amount=breakdown.supplier_cost * data["qty"],
                player_info=_mask_player_info(item.player_info or player_info),
                snapshot={
                    "supplier_product_id": variant.supplier_product_id,
                    "supplier_variation_id": variant.supplier_variation_id,
                    "price": breakdown.unit_price,
                    "currency": variant.price_currency,
                },
            )
        )

    # consume coupon count
    if coupon_code and discount > 0:
        coupon = session.execute(
            select(CouponCode).where(CouponCode.code == order.coupon_code)
        ).scalar_one_or_none()
        if coupon is not None:
            coupon.used_count += 1

    # clear cart (quick "buy now" orders use a transient QuickCart)
    if isinstance(cart, Cart):
        for item in list(cart.items):
            session.delete(item)

    notify_user(
        session,
        user,
        kind="order",
        title_key="order_created",
        body_key="order_created",
        params={
            "order_number": order.order_number,
            "total": order.total_amount,
            "currency": order.currency,
            "status": order.status.value,
        },
    )
    log.info("order created %s user=%s total=%s", order.order_number, user.id, order.total_amount)
    return order


# typing alias used above
CartItemT = Any


def _mask_player_info(player_info: Optional[dict]) -> Optional[dict]:
    """Mask player identifiers for display/storage of order summaries."""
    if not player_info:
        return {}
    masked = {}
    for key, value in player_info.items():
        text = str(value)
        if len(text) <= 4:
            masked[key] = "*" * len(text)
        else:
            masked[key] = text[:2] + "*" * (len(text) - 4) + text[-2:]
    return masked


def store_full_player_info(order: Order, player_info: dict) -> None:
    """Fulfillment needs the FULL player info; keep it only on order items' snapshot."""
    for item in order.items:
        snap = dict(item.snapshot or {})
        snap["player_info_full"] = player_info
        item.snapshot = snap


def mark_order_paid(session: Session, order: Order) -> None:
    if order.status != OrderStatus.PENDING_PAYMENT:
        return
    order.status = OrderStatus.PAID
    order.paid_at = datetime.utcnow()
    session.add(
        Fulfillment(
            order_id=order.id,
            status=FulfillmentStatus.PENDING,
            next_attempt_at=datetime.utcnow(),
        )
    )
    notify_user(
        session,
        order.user,
        kind="payment",
        title_key="payment_received",
        body_key="payment_received",
        params={
            "order_number": order.order_number,
            "total": order.total_amount,
            "currency": order.currency,
            "status": order.status.value,
        },
    )
    log.info("order paid %s", order.order_number)


def serialize_order(order: Order, *, include_internal: bool = False) -> dict:
    """Customer-safe order payload. Internal costs only for admin."""
    data = {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status.value,
        "subtotal": order.subtotal_amount,
        "discount": order.discount_amount,
        "total": order.total_amount,
        "currency": order.currency,
        "coupon_code": order.coupon_code,
        "player_info": order.player_info,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "items": [
            {
                "product_name": item.product_name,
                "variant_name": item.variant_name,
                "game_name": item.game_name,
                "quantity": item.quantity,
                "unit_price": item.unit_price_amount,
                "line_total": item.line_total_amount,
                "player_info": item.player_info,
            }
            for item in order.items
        ],
        "payment_status": order.payments[-1].status.value if order.payments else None,
        "fulfillment_status": (
            order.fulfillments[0].status.value if order.fulfillments else None
        ),
    }
    if include_internal:
        data["user_id"] = order.user_id
        data["profit"] = order.profit_amount
        data["fee"] = order.fee_amount
        data["supplier_cost"] = sum(i.supplier_cost_amount for i in order.items)
        data["idempotency_key"] = order.idempotency_key
        data["payments"] = [
            {
                "id": p.id,
                "provider": p.provider,
                "provider_payment_id": p.provider_payment_id,
                "status": p.status.value,
                "amount": p.amount,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in order.payments
        ]
    return data

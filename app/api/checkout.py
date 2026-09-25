"""Checkout + order APIs (server-side pricing, payment architecture)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import pagination, require_user
from app.api.cart import get_cart, serialize_cart
from app.db import get_db
from app.models import Order, OrderStatus, Payment, PaymentStatus, ProductVariant, User
from app.security import new_idempotency_key, rate_limit
from app.services.orders import (
    CheckoutError,
    QuickCart,
    QuickLine,
    create_order_from_cart,
    serialize_order,
    store_full_player_info,
)
from app.services.wallet import WalletError, pay_order_with_balance
from app.services.payments import PaymentError, create_payment_for_order

router = APIRouter(prefix="/api", tags=["checkout"])


class CheckoutBody(BaseModel):
    player_info: dict = Field(default_factory=dict)
    coupon_code: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=80)
    # "balance" (wallet, topped up via Hamyon) | "provider" (signed-webhook provider)
    payment_method: Optional[str] = None


class QuickCheckoutBody(CheckoutBody):
    variant_id: int


def _resolve_method(method: Optional[str]) -> str:
    from app.config import get_settings

    if method in ("balance", "provider"):
        return method
    if method:
        raise HTTPException(status_code=400, detail="invalid_payment_method")
    return "provider" if get_settings().provider_payment_configured() else "balance"


def _expand_units(cart, fallback_info: dict) -> list[QuickLine]:
    """One order per unit: the supplier fulfils exactly one top-up per order."""
    units: list[QuickLine] = []
    for item in cart.items:
        qty = max(1, min(int(item.quantity or 1), 10))
        for _ in range(qty):
            units.append(QuickLine(item.variant, 1, dict(item.player_info or fallback_info or {})))
    return units


async def _checkout_units(
    db: Session,
    user: User,
    request: Request,
    units: list[QuickLine],
    body: CheckoutBody,
    *,
    source_cart=None,
) -> dict:
    method = _resolve_method(body.payment_method)
    if not units:
        raise HTTPException(status_code=400, detail="cart_empty")
    if len(units) > 1 and body.coupon_code:
        raise HTTPException(status_code=400, detail="coupon_single_item")
    if len(units) > 1 and method == "provider":
        raise HTTPException(status_code=400, detail="provider_single_item")

    base_key = body.idempotency_key or new_idempotency_key()
    orders = []
    try:
        for index, unit in enumerate(units):
            key = base_key if len(units) == 1 else f"{base_key}:{index}"
            order = create_order_from_cart(
                db,
                user,
                QuickCart([unit]),
                player_info=unit.player_info,
                coupon_code=(body.coupon_code or (source_cart.coupon_code if source_cart else None))
                if len(units) == 1 else None,
                idempotency_key=key,
            )
            store_full_player_info(order, unit.player_info)
            orders.append(order)
    except CheckoutError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.key)

    if method == "balance":
        need = sum(o.total_amount for o in orders if o.status == OrderStatus.PENDING_PAYMENT)
        db.refresh(user)
        if need > int(user.balance or 0):
            db.rollback()
            raise HTTPException(
                status_code=402,
                detail={"code": "insufficient_balance", "balance": int(user.balance or 0),
                        "required": need, "missing": need - int(user.balance or 0)},
            )
        try:
            for order in orders:
                pay_order_with_balance(db, user, order)
        except WalletError as exc:
            db.rollback()
            raise HTTPException(status_code=402 if exc.key == "insufficient_balance" else 400,
                                detail=exc.key)
        if source_cart is not None:
            for item in list(source_cart.items):
                db.delete(item)
        db.commit()
        return {
            "order": serialize_order(orders[0]),
            "orders": [serialize_order(o) for o in orders],
            "payment": {"provider": "balance", "status": "PAID",
                        "amount": sum(o.total_amount for o in orders), "currency": "UZS"},
            "checkout_url": None,
            "balance": int(user.balance or 0),
            "error": None,
        }

    # provider flow (single order)
    order = orders[0]
    if source_cart is not None:
        for item in list(source_cart.items):
            db.delete(item)
    if order.payments and order.payments[-1].status == PaymentStatus.PENDING:
        payment = order.payments[-1]
    else:
        try:
            payment = await create_payment_for_order(
                db,
                order,
                return_url=f"{request.base_url}orders/{order.order_number}",
                description=f"VYRON {order.order_number}",
            )
        except PaymentError as exc:
            db.commit()  # honest state: order exists, payment NOT configured
            return {"order": serialize_order(order), "orders": [serialize_order(order)],
                    "payment": None, "error": exc.key, "checkout_url": None}
    db.commit()
    return {
        "order": serialize_order(order),
        "orders": [serialize_order(order)],
        "payment": {
            "id": payment.id,
            "provider": payment.provider,
            "status": payment.status.value,
            "amount": payment.amount,
            "currency": payment.currency,
        },
        "checkout_url": payment.checkout_url,
        "error": None,
    }


@router.post("/checkout")
async def checkout(
    body: CheckoutBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Checkout the server-side cart. Prices are always recomputed server-side."""
    if not rate_limit(f"checkout:{user.id}", 10, 60):
        raise HTTPException(status_code=429, detail="rate_limited")
    if body.idempotency_key:
        existing = db.execute(
            select(Order).where(Order.idempotency_key == body.idempotency_key, Order.user_id == user.id)
        ).scalar_one_or_none()
        if existing is not None:  # idempotent replay
            payment = existing.payments[-1] if existing.payments else None
            return {"order": serialize_order(existing), "orders": [serialize_order(existing)],
                    "payment": {"id": payment.id, "provider": payment.provider,
                                "status": payment.status.value, "amount": payment.amount,
                                "currency": payment.currency} if payment else None,
                    "checkout_url": payment.checkout_url if payment else None, "error": None}
    cart = get_cart(db, user)
    units = _expand_units(cart, body.player_info)
    return await _checkout_units(db, user, request, units, body, source_cart=cart)


@router.post("/checkout/quick")
async def quick_checkout(
    body: QuickCheckoutBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """One-click "Buy now" for a single variant (does not touch the cart)."""
    if not rate_limit(f"checkout:{user.id}", 10, 60):
        raise HTTPException(status_code=429, detail="rate_limited")
    variant = db.get(ProductVariant, body.variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="variant_unavailable")
    unit = QuickLine(variant, 1, dict(body.player_info or {}))
    return await _checkout_units(db, user, request, [unit], body)


@router.get("/orders")
def my_orders(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Order).where(Order.user_id == user.id)
    if status:
        try:
            query = query.where(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_status")
    from sqlalchemy import func

    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(
            query.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return {
        "items": [serialize_order(o) for o in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/orders/{order_number}")
def order_detail(
    order_number: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    order = db.execute(
        select(Order).where(Order.order_number == order_number)
    ).scalar_one_or_none()
    if order is None or (order.user_id != user.id and not user.is_staff):
        raise HTTPException(status_code=404, detail="order_not_found")
    return {"order": serialize_order(order, include_internal=user.is_staff)}

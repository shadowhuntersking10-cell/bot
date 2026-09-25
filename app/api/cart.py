"""Cart API: server-side cart with availability + price revalidation."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Cart, CartItem, Product, ProductVariant, User
from app.api.deps import require_user
from app.services.orders import validate_player_info
from app.services.pricing import compute_price, effective_price

router = APIRouter(prefix="/api/cart", tags=["cart"])


class AddItemBody(BaseModel):
    variant_id: int
    quantity: int = Field(default=1, ge=1, le=50)
    player_info: Optional[dict] = None


class UpdateItemBody(BaseModel):
    quantity: Optional[int] = Field(default=None, ge=1, le=50)
    player_info: Optional[dict] = None


class CouponBody(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    # Preview for a single "Buy now" unit (does not modify the cart)
    variant_id: Optional[int] = None


def get_cart(db: Session, user: User) -> Cart:
    cart = db.execute(select(Cart).where(Cart.user_id == user.id)).scalar_one_or_none()
    if cart is None:
        cart = Cart(user_id=user.id)
        db.add(cart)
        db.flush()
    return cart


def serialize_cart(db: Session, cart: Cart) -> dict:
    items = []
    subtotal = 0
    valid = True
    for item in cart.items:
        variant = item.variant
        product = variant.product if variant else None
        price = effective_price(db, variant) if variant else None
        available = bool(
            variant and variant.active and variant.in_stock and price is not None
            and product and product.active and product.visibility
        )
        if not available:
            valid = False
        line_total = (price or 0) * item.quantity
        if available:
            subtotal += line_total
        items.append(
            {
                "id": item.id,
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "player_info": item.player_info,
                "available": available,
                "unit_price": price,
                "line_total": line_total if available else None,
                "variant": {
                    "id": variant.id,
                    "name": variant.name,
                    "amount_label": variant.amount_label,
                    "currency": variant.price_currency,
                }
                if variant
                else None,
                "product": {
                    "id": product.id,
                    "name": product.name,
                    "image_url": product.image_url,
                    "required_fields": (product.required_fields or {}).get("fields", []),
                }
                if product
                else None,
                "game": {"slug": product.game.slug, "name": product.game.name}
                if product and product.game
                else None,
            }
        )
    return {
        "items": items,
        "subtotal": subtotal,
        "count": sum(i.quantity for i in cart.items),
        "coupon_code": cart.coupon_code,
        "valid": valid,
    }


@router.get("")
def read_cart(db: Session = Depends(get_db), user: User = Depends(require_user)):
    cart = get_cart(db, user)
    return {"cart": serialize_cart(db, cart)}


@router.post("/items")
def add_item(
    body: AddItemBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    variant = db.get(ProductVariant, body.variant_id)
    if variant is None or not variant.active or not variant.in_stock:
        raise HTTPException(status_code=400, detail="variant_unavailable")
    product = variant.product
    if product is None or not product.active or not product.visibility:
        raise HTTPException(status_code=400, detail="product_unavailable")
    if variant.supplier_cost is None or effective_price(db, variant) is None:
        raise HTTPException(status_code=400, detail="not_available")
    err = validate_player_info(product, body.player_info or {})
    if err:
        raise HTTPException(status_code=400, detail=err)

    cart = get_cart(db, user)
    for item in cart.items:
        if item.variant_id == variant.id:
            item.quantity = min(50, item.quantity + body.quantity)
            if body.player_info:
                item.player_info = body.player_info
            db.commit()
            return {"cart": serialize_cart(db, cart)}
    cart.items.append(
        CartItem(variant_id=variant.id, quantity=body.quantity, player_info=body.player_info)
    )
    db.commit()
    return {"cart": serialize_cart(db, cart)}


@router.patch("/items/{item_id}")
def update_item(
    item_id: int,
    body: UpdateItemBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    cart = get_cart(db, user)
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="item_not_found")
    if body.quantity is not None:
        item.quantity = body.quantity
    if body.player_info is not None:
        item.player_info = body.player_info
    db.commit()
    return {"cart": serialize_cart(db, cart)}


@router.delete("/items/{item_id}")
def remove_item(
    item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    cart = get_cart(db, user)
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="item_not_found")
    db.delete(item)
    db.flush()
    db.expire(cart, ["items"])
    db.commit()
    return {"cart": serialize_cart(db, cart)}


@router.post("/clear")
def clear_cart(db: Session = Depends(get_db), user: User = Depends(require_user)):
    cart = get_cart(db, user)
    for item in list(cart.items):
        db.delete(item)
    cart.coupon_code = None
    db.flush()
    db.expire(cart, ["items"])
    db.commit()
    return {"cart": serialize_cart(db, cart)}


@router.post("/coupon")
def apply_coupon(
    body: CouponBody, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    from datetime import datetime

    from app.models import CouponCode, Order, OrderStatus
    from app.services.pricing import compute_discount

    cart = get_cart(db, user)
    if body.variant_id is not None:
        variant = db.get(ProductVariant, body.variant_id)
        price = effective_price(db, variant) if variant is not None else None
        if price is None or not variant.active or not variant.in_stock:
            raise HTTPException(status_code=400, detail="variant_unavailable")
        data = {"valid": True, "subtotal": price, "count": 1}
    else:
        data = serialize_cart(db, cart)
    if not data["valid"]:
        raise HTTPException(status_code=400, detail="cart_invalid")
    if data["count"] > 1:
        raise HTTPException(status_code=400, detail="coupon_single_item")
    coupon = db.execute(
        select(CouponCode).where(CouponCode.code == body.code.upper().strip())
    ).scalar_one_or_none()
    if coupon is None:
        raise HTTPException(status_code=400, detail="coupon_invalid")
    used = (
        db.execute(
            select(Order)
            .where(
                Order.user_id == user.id,
                Order.coupon_code == coupon.code,
                Order.status.notin_([OrderStatus.CANCELLED, OrderStatus.FAILED]),
            )
        )
        .scalars()
        .all()
    )
    discount, err = compute_discount(db, coupon, data["subtotal"], user.id, len(used))
    if err:
        raise HTTPException(status_code=400, detail=err)
    if body.variant_id is None:
        cart.coupon_code = coupon.code
    db.commit()
    return {"cart": serialize_cart(db, cart), "discount": discount, "coupon": coupon.code}


@router.post("/validate")
def validate_cart(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Revalidate availability and CURRENT prices server-side."""
    cart = get_cart(db, user)
    return {"cart": serialize_cart(db, cart)}

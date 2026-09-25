"""Pricing engine: supplier cost + payment fee + VYRON margin = customer price.

Supplier cost and profit are internal. NEVER expose them to customers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminSetting, CouponCode, CouponType, Game, Product, ProductVariant


@dataclass
class PriceBreakdown:
    unit_price: int
    supplier_cost: int
    fee: int
    margin: int
    profit: int


def get_pricing_settings(session: Session) -> dict:
    row = session.execute(
        select(AdminSetting).where(AdminSetting.key == "pricing")
    ).scalar_one_or_none()
    defaults = {
        "margin_percent": 10,
        "margin_fixed": 0,
        "payment_fee_percent": 0,
        "payment_fee_fixed": 0,
        "min_margin_percent": 0,
        "allow_below_min_margin": False,
    }
    if row and isinstance(row.value, dict):
        defaults.update(row.value)
    return defaults


def compute_price(
    session: Session,
    variant: ProductVariant,
    *,
    override_price: Optional[int] = None,
) -> PriceBreakdown:
    """Compute the customer price from the CURRENT supplier cost.

    Precedence for margin: variant > product > global settings.
    """
    pricing = get_pricing_settings(session)
    supplier_cost = variant.supplier_cost if variant.supplier_cost is not None else 0

    margin_percent = pricing.get("margin_percent", 10)
    margin_fixed = pricing.get("margin_fixed", 0)

    product: Optional[Product] = variant.product
    if product is not None:
        if product.margin_percent is not None:
            margin_percent = product.margin_percent
        if product.margin_fixed is not None:
            margin_fixed = product.margin_fixed
    if variant.margin_percent is not None:
        margin_percent = variant.margin_percent
    if variant.margin_fixed is not None:
        margin_fixed = variant.margin_fixed

    margin = round(supplier_cost * margin_percent / 100) + int(margin_fixed)
    fee = round(supplier_cost * pricing.get("payment_fee_percent", 0) / 100) + int(
        pricing.get("payment_fee_fixed", 0)
    )
    unit_price = override_price if override_price is not None else supplier_cost + fee + margin
    if unit_price < 0:
        unit_price = 0
    profit = unit_price - supplier_cost - fee
    return PriceBreakdown(
        unit_price=unit_price,
        supplier_cost=supplier_cost,
        fee=fee,
        margin=margin,
        profit=profit,
    )


def is_margin_below_minimum(session: Session, breakdown: PriceBreakdown) -> bool:
    pricing = get_pricing_settings(session)
    min_percent = pricing.get("min_margin_percent", 0)
    if min_percent <= 0 or breakdown.supplier_cost <= 0:
        return False
    min_margin = round(breakdown.supplier_cost * min_percent / 100)
    return (breakdown.profit) < min_margin


def compute_discount(
    session: Session,
    coupon: CouponCode,
    subtotal: int,
    user_id: int,
    user_usage_count: int,
) -> tuple[int, Optional[str]]:
    """Validate coupon server-side. Returns (discount, error_key)."""
    from datetime import datetime

    if not coupon.active:
        return 0, "coupon_invalid"
    if coupon.expires_at is not None and coupon.expires_at < datetime.utcnow():
        return 0, "coupon_expired"
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        return 0, "coupon_exhausted"
    if user_usage_count >= coupon.per_user_limit:
        return 0, "coupon_user_limit"
    if subtotal < coupon.min_order_amount:
        return 0, "coupon_min_order"
    if coupon.coupon_type == CouponType.PERCENT:
        discount = round(subtotal * coupon.value / 100)
    else:
        discount = int(coupon.value)
    discount = max(0, min(discount, subtotal))
    return discount, None


def effective_price(session: Session, variant: ProductVariant) -> Optional[int]:
    """Public price for a variant. None when not sellable (no real supplier cost)."""
    if not variant.active or not variant.in_stock:
        return None
    if variant.supplier_cost is None:
        return None
    breakdown = compute_price(session, variant)
    return breakdown.unit_price

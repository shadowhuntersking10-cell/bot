"""Admin API — every route protected server-side (require_admin).

Admin actions are written to immutable audit logs. Secrets are never exposed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import pagination, require_admin
from app.config import get_settings
from app.db import get_db
from app import db as db_module
from app.i18n import normalize_lang
from app.logging_config import get_logger
from app.models import (
    AdminSetting,
    AuditLog,
    CatalogSyncLog,
    CouponCode,
    CouponCode as Coupon,
    CouponType,
    Fulfillment,
    FulfillmentStatus,
    Game,
    Notification,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    ProductType,
    ProductVariant,
    Promotion,
    Role,
    RoleName,
    Supplier,
    SupplierProduct,
    SupplierTransaction,
    TelegramUser,
    User,
    WebhookEvent,
)
from app.providers.suppliers.base import SupplierError
from app.providers.suppliers.payerpin import get_payerpin
from app.services.audit import write_audit
from app.services.catalog_sync import CatalogSyncError, sync_payerpin_catalog
from app.services.fulfillment import FulfillmentService
from app.services.orders import serialize_order
from app.services.notifications import notify_user
from app.services.pricing import compute_price, get_pricing_settings

log = get_logger("vyron.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class GameBody(BaseModel):
    slug: Optional[str] = None
    name: str
    name_uz: Optional[str] = None
    name_ru: Optional[str] = None
    description_uz: Optional[str] = None
    description_en: Optional[str] = None
    description_ru: Optional[str] = None
    category: Optional[str] = None
    currency_label: Optional[str] = None
    icon_url: Optional[str] = None
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None
    sort_order: Optional[int] = None
    featured: Optional[bool] = None
    active: Optional[bool] = None


class ProductBody(BaseModel):
    game_id: Optional[int] = None
    name: str
    name_uz: Optional[str] = None
    name_ru: Optional[str] = None
    description_uz: Optional[str] = None
    description_en: Optional[str] = None
    description_ru: Optional[str] = None
    product_type: Optional[str] = "TOPUP"
    fulfillment_type: Optional[str] = "AUTO"
    image_url: Optional[str] = None
    required_fields: Optional[dict] = None
    margin_percent: Optional[int] = None
    margin_fixed: Optional[int] = None
    visibility: Optional[bool] = None
    active: Optional[bool] = None


class VariantBody(BaseModel):
    product_id: int
    name: str
    name_uz: Optional[str] = None
    name_ru: Optional[str] = None
    amount_label: Optional[str] = None
    supplier_product_id: Optional[str] = None
    supplier_variation_id: Optional[str] = None
    supplier_cost: Optional[int] = None
    supplier_currency: Optional[str] = None
    region: Optional[str] = None
    margin_percent: Optional[int] = None
    margin_fixed: Optional[int] = None
    in_stock: Optional[bool] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None


class PricingBody(BaseModel):
    margin_percent: Optional[int] = Field(default=None, ge=0, le=1000)
    margin_fixed: Optional[int] = Field(default=None, ge=0)
    payment_fee_percent: Optional[int] = Field(default=None, ge=0, le=1000)
    payment_fee_fixed: Optional[int] = Field(default=None, ge=0)
    min_margin_percent: Optional[int] = Field(default=None, ge=0, le=1000)
    allow_below_min_margin: Optional[bool] = None


class CouponBody(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    coupon_type: str = "PERCENT"
    value: int = Field(ge=0)
    min_order_amount: int = Field(default=0, ge=0)
    max_uses: Optional[int] = Field(default=None, ge=0)
    per_user_limit: int = Field(default=1, ge=1)
    allow_below_margin: bool = False
    expires_at: Optional[datetime] = None
    active: bool = True


class PromotionBody(BaseModel):
    title: str
    title_uz: Optional[str] = None
    title_ru: Optional[str] = None
    description_uz: Optional[str] = None
    description_en: Optional[str] = None
    description_ru: Optional[str] = None
    image_url: Optional[str] = None
    coupon_code: Optional[str] = None
    game_id: Optional[int] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    active: bool = True


class UserPatchBody(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None
    language: Optional[str] = None


class OrderActionBody(BaseModel):
    reason: Optional[str] = None


class SettingsBody(BaseModel):
    value: dict


class BroadcastBody(BaseModel):
    # Free-text announcement (sent in-app + Telegram to every active user)
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Dashboard / analytics / health
# ---------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    stats = {
        "users": db.execute(select(func.count(User.id))).scalar() or 0,
        "orders_total": db.execute(select(func.count(Order.id))).scalar() or 0,
        "orders_24h": db.execute(
            select(func.count(Order.id)).where(Order.created_at >= day_ago)
        ).scalar()
        or 0,
        "orders_pending": db.execute(
            select(func.count(Order.id)).where(
                Order.status == OrderStatus.PENDING_PAYMENT
            )
        ).scalar()
        or 0,
        "orders_processing": db.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(
                    [
                        OrderStatus.PAID,
                        OrderStatus.FULFILLMENT_PENDING,
                        OrderStatus.SUPPLIER_PROCESSING,
                    ]
                )
            )
        ).scalar()
        or 0,
        "orders_completed": db.execute(
            select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED)
        ).scalar()
        or 0,
        "orders_failed": db.execute(
            select(func.count(Order.id)).where(Order.status == OrderStatus.FAILED)
        ).scalar()
        or 0,
        "revenue_total": db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.status.in_([OrderStatus.PAID, OrderStatus.FULFILLMENT_PENDING,
                                  OrderStatus.SUPPLIER_PROCESSING, OrderStatus.COMPLETED])
            )
        ).scalar()
        or 0,
        "revenue_24h": db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.paid_at >= day_ago,
                Order.status.notin_([OrderStatus.CANCELLED, OrderStatus.PENDING_PAYMENT]),
            )
        ).scalar()
        or 0,
        "profit_total": db.execute(
            select(func.coalesce(func.sum(Order.profit_amount), 0)).where(
                Order.status.in_([OrderStatus.PAID, OrderStatus.FULFILLMENT_PENDING,
                                  OrderStatus.SUPPLIER_PROCESSING, OrderStatus.COMPLETED])
            )
        ).scalar()
        or 0,
        "products_active": db.execute(
            select(func.count(ProductVariant.id)).where(
                ProductVariant.active == True,  # noqa: E712
                ProductVariant.supplier_cost.is_not(None),
            )
        ).scalar()
        or 0,
        "products_total": db.execute(select(func.count(ProductVariant.id))).scalar() or 0,
        "fulfillments_pending": db.execute(
            select(func.count(Fulfillment.id)).where(
                Fulfillment.status.in_(
                    [FulfillmentStatus.PENDING, FulfillmentStatus.PROCESSING, FulfillmentStatus.AWAITING_STATUS]
                )
            )
        ).scalar()
        or 0,
    }
    recent_orders = (
        db.execute(select(Order).order_by(Order.id.desc()).limit(8))
        .scalars()
        .all()
    )
    from app.services.wallet import wallet_stats

    stats.update(wallet_stats(db))
    settings = get_settings()
    return {
        "stats": stats,
        "recent_orders": [serialize_order(o) for o in recent_orders],
        "integrations": {
            "payerpin": settings.payerpin_configured(),
            "hamyon": settings.hamyon_configured(),
            "telegram": settings.telegram_configured(),
            "public_base_url": bool(settings.public_base_url),
        },
    }


@router.get("/analytics")
def analytics(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    days: int = Query(default=14, ge=1, le=90),
):
    out = []
    for i in range(days - 1, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).date()
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        revenue = db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.paid_at >= start,
                Order.paid_at < end,
                Order.status.notin_([OrderStatus.CANCELLED, OrderStatus.PENDING_PAYMENT]),
            )
        ).scalar() or 0
        count = db.execute(
            select(func.count(Order.id)).where(Order.created_at >= start, Order.created_at < end)
        ).scalar() or 0
        out.append({"date": day.isoformat(), "revenue": revenue, "orders": count})
    top_products = db.execute(
        select(
            OrderItem.variant_name,
            func.sum(OrderItem.line_total_amount).label("revenue"),
            func.sum(OrderItem.quantity).label("qty"),
        )
        .group_by(OrderItem.variant_name)
        .order_by(func.sum(OrderItem.line_total_amount).desc())
        .limit(10)
    ).all()
    return {
        "daily": out,
        "top_products": [
            {"variant": row[0], "revenue": int(row[1] or 0), "quantity": int(row[2] or 0)}
            for row in top_products
        ],
    }


@router.get("/health")
def health(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """System configuration check — Configured / Not configured only. No secrets."""
    settings = get_settings()
    checks = {}

    try:
        db.execute(select(func.count(User.id)))
        checks["DATABASE"] = {"status": "Connected", "detail": db_module.db_dialect}
    except Exception as exc:
        checks["DATABASE"] = {"status": "Error", "detail": type(exc).__name__}

    checks["HAMYON"] = {
        "status": "Configured" if settings.hamyon_configured() else "Not configured",
        "detail": settings.hamyon_base_url,
    }
    checks["PAYMENT"] = {
        "status": "Configured" if settings.provider_payment_configured() else "Not configured",
        "detail": settings.payment_provider or "-",
    }
    checks["PAYERPIN"] = {
        "status": "Configured" if settings.payerpin_configured() else "Not configured",
        "detail": settings.payerpin_base_url,
    }
    checks["TELEGRAM"] = {
        "status": "Configured" if settings.telegram_configured() else "Not configured",
        "detail": "admin IDs set" if settings.telegram_admin_ids else "no admin IDs",
    }
    checks["AUTH"] = {
        "status": "Configured",
        "detail": "sessions+bcrypt",
    }
    checks["WEBHOOKS"] = {
        "status": "Configured" if settings.payment_configured() else "Not configured",
        "detail": "/api/webhooks/hamyon/complete, /api/webhooks/payments, /api/webhooks/payerpin",
    }
    checks["STORAGE"] = {
        "status": "Configured" if settings.upload_dir.exists() else "Not configured",
        "detail": "uploads/ (admin media)",
    }
    return {"checks": checks, "env": settings.app_env, "time": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(User)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            or_(User.username.ilike(like), User.email.ilike(like))
        )
        if search.strip().isdigit():
            query = select(User).where(or_(User.username.ilike(like), User.email.ilike(like),
                                           User.telegram_id == int(search.strip()),
                                           User.id == int(search.strip())))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role.name.value if u.role else "CUSTOMER",
                "is_active": u.is_active,
                "telegram_linked": bool(u.telegram_id),
                "telegram_id": u.telegram_id,
                "balance": int(u.balance or 0),
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/users/{user_id}")
def patch_user(
    user_id: int,
    body: UserPatchBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.language is not None:
        user.language = normalize_lang(body.language)
    if body.role is not None:
        try:
            role_name = RoleName(body.role)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_role")
        role = db.execute(select(Role).where(Role.name == role_name)).scalar_one()
        user.role_id = role.id
    write_audit(
        db,
        admin_user_id=admin.id,
        action="user.updated",
        target_type="user",
        target_id=user.id,
        details=body.model_dump(exclude_none=True),
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Games / products / variants
# ---------------------------------------------------------------------------
@router.get("/games")
def admin_games(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Game)
    if search:
        query = query.where(Game.name.ilike(f"%{search.strip()}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(Game.sort_order).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": g.id,
                "slug": g.slug,
                "name": g.name,
                "category": g.category,
                "currency_label": g.currency_label,
                "icon_url": g.icon_url,
                "logo_url": g.logo_url,
                "banner_url": g.banner_url,
                "name_uz": g.name_uz,
                "name_ru": g.name_ru,
                "description_uz": g.description_uz,
                "description_en": g.description_en,
                "description_ru": g.description_ru,
                "sort_order": g.sort_order,
                "featured": g.featured,
                "active": g.active,
                "supplier_game_key": g.supplier_game_key,
                "products": len(g.products),
            }
            for g in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/games")
def create_game(
    body: GameBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    slug = (body.slug or body.name.lower().replace(" ", "-").replace(":", ""))[:90]
    if db.execute(select(Game).where(Game.slug == slug)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="slug_exists")
    game = Game(slug=slug, name=body.name, **body.model_dump(exclude={"slug", "name"}, exclude_none=True))
    db.add(game)
    write_audit(db, admin_user_id=admin.id, action="game.created", target_type="game", target_id=slug)
    db.commit()
    return {"ok": True, "id": game.id}


@router.patch("/games/{game_id}")
def patch_game(
    game_id: int,
    body: GameBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game_not_found")
    for key, value in body.model_dump(exclude_none=True).items():
        if key == "slug":
            continue
        setattr(game, key, value)
    write_audit(db, admin_user_id=admin.id, action="game.updated", target_type="game", target_id=game.id,
                details=body.model_dump(exclude_none=True))
    db.commit()
    return {"ok": True}


@router.get("/products")
def admin_products(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    search: Optional[str] = Query(default=None),
    game_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Product)
    if search:
        query = query.where(Product.name.ilike(f"%{search.strip()}%"))
    if game_id:
        query = query.where(Product.game_id == game_id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(Product.id).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "game": p.game.name if p.game else None,
                "product_type": p.product_type.value,
                "fulfillment_type": p.fulfillment_type.value,
                "active": p.active,
                "visibility": p.visibility,
                "margin_percent": p.margin_percent,
                "margin_fixed": p.margin_fixed,
                "variants": len(p.variants),
                "image_url": p.image_url,
                "game_id": p.game_id,
                "name_uz": p.name_uz,
                "name_ru": p.name_ru,
                "description_uz": p.description_uz,
                "description_en": p.description_en,
                "description_ru": p.description_ru,
                "required_fields": p.required_fields,
            }
            for p in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/products")
def create_product(
    body: ProductBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    if body.game_id is not None and db.get(Game, body.game_id) is None:
        raise HTTPException(status_code=400, detail="game_not_found")
    slug = body.name.lower().replace(" ", "-")[:100]
    product = Product(
        slug=slug,
        **body.model_dump(exclude_none=True),
        active=body.active or False,
    )
    db.add(product)
    write_audit(db, admin_user_id=admin.id, action="product.created", target_type="product", target_id=slug)
    db.commit()
    return {"ok": True, "id": product.id}


@router.patch("/products/{product_id}")
def patch_product(
    product_id: int,
    body: ProductBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product_not_found")
    data = body.model_dump(exclude_none=True)
    if "product_type" in data:
        data["product_type"] = ProductType(data["product_type"])
    for key, value in data.items():
        setattr(product, key, value)
    write_audit(db, admin_user_id=admin.id, action="product.updated", target_type="product",
                target_id=product.id, details=data)
    db.commit()
    return {"ok": True}


@router.get("/variants")
def admin_variants(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    product_id: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(ProductVariant)
    if product_id:
        query = query.where(ProductVariant.product_id == product_id)
    if search:
        query = query.where(ProductVariant.name.ilike(f"%{search.strip()}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(ProductVariant.id).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    items = []
    for v in rows:
        breakdown = compute_price(db, v) if v.supplier_cost is not None else None
        items.append(
            {
                "id": v.id,
                "product_id": v.product_id,
                "product": v.product.name if v.product else None,
                "name": v.name,
                "name_uz": v.name_uz,
                "name_ru": v.name_ru,
                "amount_label": v.amount_label,
                "margin_percent": v.margin_percent,
                "margin_fixed": v.margin_fixed,
                "sort_order": v.sort_order,
                "supplier_product_id": v.supplier_product_id,
                "supplier_variation_id": v.supplier_variation_id,
                "supplier_cost": v.supplier_cost,
                "price": v.price_amount if v.price_amount is not None
                else (breakdown.unit_price if breakdown else None),
                "profit": breakdown.profit if breakdown else None,
                "currency": v.price_currency,
                "region": v.region,
                "active": v.active,
                "in_stock": v.in_stock,
                "mapped": bool(v.supplier_product_id),
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/variants")
def create_variant(
    body: VariantBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(status_code=400, detail="product_not_found")
    if (body.supplier_product_id is None) != (body.supplier_cost is None):
        raise HTTPException(status_code=400, detail="invalid_supplier_mapping")
    data = body.model_dump()
    data["amount_label"] = body.amount_label or body.name
    variant = ProductVariant(**data)
    db.add(variant)
    db.flush()
    if variant.supplier_cost is not None:
        breakdown = compute_price(db, variant)
        variant.price_amount = breakdown.unit_price
    write_audit(db, admin_user_id=admin.id, action="variant.created", target_type="variant",
                target_id=variant.id, details={"name": variant.name})
    db.commit()
    return {"ok": True, "id": variant.id}


@router.patch("/variants/{variant_id}")
def patch_variant(
    variant_id: int,
    body: VariantBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    variant = db.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="variant_not_found")
    data = body.model_dump(exclude_none=True)
    data.pop("product_id", None)
    for key, value in data.items():
        setattr(variant, key, value)
    if variant.supplier_cost is not None:
        breakdown = compute_price(db, variant)
        variant.price_amount = breakdown.unit_price
    write_audit(db, admin_user_id=admin.id, action="variant.updated", target_type="variant",
                target_id=variant.id, details=data)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Orders / payments / fulfillments
# ---------------------------------------------------------------------------
@router.get("/orders")
def admin_orders(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    search: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Order)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(Order.order_number.ilike(like))
    if status:
        try:
            query = query.where(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_status")
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                **serialize_order(o, include_internal=True),
                "user": o.user.username if o.user else None,
            }
            for o in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/orders/{order_id}")
def admin_order_detail(
    order_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    data = serialize_order(order, include_internal=True)
    data["user"] = order.user.username if order.user else None
    data["supplier_transactions"] = [
        {
            "id": tx.id,
            "supplier_order_id": tx.supplier_order_id,
            "request_status": tx.request_status,
            "response_status": tx.response_status,
            "fulfillment_status": tx.fulfillment_status.value,
            "error_code": tx.error_code,
            "error_message": tx.error_message,
            "attempts": tx.attempts,
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
        }
        for tx in db.execute(
            select(SupplierTransaction).where(SupplierTransaction.order_id == order.id)
        )
        .scalars()
        .all()
    ]
    return {"order": data}


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    body: OrderActionBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if order.status not in (OrderStatus.PENDING_PAYMENT, OrderStatus.FAILED):
        raise HTTPException(status_code=400, detail="cannot_cancel")
    order.status = OrderStatus.CANCELLED
    write_audit(db, admin_user_id=admin.id, action="order.cancelled", target_type="order",
                target_id=order.order_number, details={"reason": body.reason})
    notify_user(
        db, order.user, kind="order", title_key="order_failed", body_key="order_failed",
        params={"order_number": order.order_number, "status": order.status.value},
    )
    db.commit()
    return {"ok": True}


@router.post("/orders/{order_id}/refund")
def refund_order(
    order_id: int,
    body: OrderActionBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Refund to the customer's VYRON wallet (refund logic separated from fulfillment)."""
    from app.services.wallet import refund_order_to_balance

    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if order.status not in (OrderStatus.COMPLETED, OrderStatus.FAILED, OrderStatus.SUPPLIER_PROCESSING,
                            OrderStatus.PAID, OrderStatus.FULFILLMENT_PENDING):
        raise HTTPException(status_code=400, detail="cannot_refund")
    if not refund_order_to_balance(db, order, reason=body.reason or "admin refund",
                                   admin_user_id=admin.id):
        raise HTTPException(status_code=400, detail="cannot_refund")
    fulfillment = order.fulfillments[0] if order.fulfillments else None
    if fulfillment and fulfillment.status in (FulfillmentStatus.PENDING,):
        fulfillment.status = FulfillmentStatus.FAILED
        fulfillment.last_error = "refunded_by_admin"
    write_audit(db, admin_user_id=admin.id, action="order.refunded", target_type="order",
                target_id=order.order_number, details={"reason": body.reason})
    db.commit()
    return {"ok": True}


@router.post("/orders/{order_id}/retry-fulfillment")
def retry_fulfillment(
    order_id: int,
    body: OrderActionBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if order.status not in (OrderStatus.FAILED, OrderStatus.SUPPLIER_PROCESSING):
        raise HTTPException(status_code=400, detail="cannot_retry")
    if not get_settings().payerpin_configured():
        raise HTTPException(status_code=503, detail="supplier_not_configured")
    order.status = OrderStatus.PAID
    fulfillment = order.fulfillments[0] if order.fulfillments else None
    if fulfillment:
        fulfillment.status = FulfillmentStatus.PENDING
        fulfillment.attempts = 0
        fulfillment.next_attempt_at = datetime.utcnow()
        fulfillment.last_error = None
        fulfillment.locked_by = None
    write_audit(db, admin_user_id=admin.id, action="order.retry_fulfillment",
                target_type="order", target_id=order.order_number)
    db.commit()
    return {"ok": True}


@router.get("/payments")
def admin_payments(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Payment)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(Payment.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": p.id,
                "order_number": p.order.order_number if p.order else None,
                "provider": p.provider,
                "provider_payment_id": p.provider_payment_id,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status.value,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            }
            for p in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/fulfillments")
def admin_fulfillments(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Fulfillment)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(Fulfillment.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": f.id,
                "order_number": f.order.order_number if f.order else None,
                "status": f.status.value,
                "attempts": f.attempts,
                "max_attempts": f.max_attempts,
                "last_error": f.last_error,
                "next_attempt_at": f.next_attempt_at.isoformat() if f.next_attempt_at else None,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# Payerpin admin page
# ---------------------------------------------------------------------------
@router.get("/suppliers/payerpin")
def payerpin_status(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    settings = get_settings()
    supplier = db.execute(select(Supplier).where(Supplier.code == "payerpin")).scalar_one_or_none()
    config = supplier.config if supplier and isinstance(supplier.config, dict) else {}

    active_supplier_products = db.execute(
        select(func.count(SupplierProduct.id)).where(
            SupplierProduct.supplier_id == supplier.id if supplier else False,
            SupplierProduct.active == True,  # noqa: E712
        )
    ).scalar() or 0 if supplier else 0

    pending = db.execute(
        select(func.count(Fulfillment.id)).where(
            Fulfillment.status.in_([FulfillmentStatus.PENDING, FulfillmentStatus.PROCESSING, FulfillmentStatus.AWAITING_STATUS])
        )
    ).scalar() or 0
    failed = db.execute(
        select(func.count(Fulfillment.id)).where(Fulfillment.status == FulfillmentStatus.FAILED)
    ).scalar() or 0
    completed = db.execute(
        select(func.count(SupplierTransaction.id)).where(
            SupplierTransaction.fulfillment_status == FulfillmentStatus.COMPLETED
        )
    ).scalar() or 0

    last_sync = db.execute(
        select(CatalogSyncLog).order_by(CatalogSyncLog.id.desc()).limit(1)
    ).scalars().first()

    return {
        "connection_status": "Configured" if settings.payerpin_configured() else "NOT CONFIGURED",
        "api_configured": settings.payerpin_configured(),  # YES/NO only — never the key
        "api_key_configured": settings.payerpin_configured(),
        "base_url": settings.payerpin_base_url,
        "balance": config.get("balance"),
        "balance_currency": config.get("balance_currency"),
        "balance_checked_at": config.get("balance_checked_at"),
        "low_balance_warning": config.get("low_balance_warning", False),
        "last_success_request": config.get("last_success_request"),
        "last_failed_request": config.get("last_failed_request"),
        "last_catalog_sync": config.get("last_catalog_sync"),
        "last_sync": {
            "status": last_sync.status,
            "games": last_sync.games_found,
            "products": last_sync.products_found,
            "variants": last_sync.variants_found,
            "started_at": last_sync.started_at.isoformat() if last_sync.started_at else None,
            "error": last_sync.error,
        }
        if last_sync
        else None,
        "active_supplier_products": active_supplier_products,
        "pending_fulfillments": pending,
        "failed_fulfillments": failed,
        "completed_supplier_orders": completed,
        "sync_history": [
            {
                "id": s.id,
                "status": s.status,
                "games": s.games_found,
                "products": s.products_found,
                "variants": s.variants_found,
                "activated": s.products_activated,
                "deactivated": s.products_deactivated,
                "error": s.error,
                "started_at": s.started_at.isoformat() if s.started_at else None,
            }
            for s in db.execute(
                select(CatalogSyncLog).order_by(CatalogSyncLog.id.desc()).limit(10)
            )
            .scalars()
            .all()
        ],
    }


def _mark_supplier_request(db: Session, success: bool) -> None:
    supplier = db.execute(select(Supplier).where(Supplier.code == "payerpin")).scalar_one_or_none()
    if supplier is None:
        return
    config = dict(supplier.config or {})
    key = "last_success_request" if success else "last_failed_request"
    config[key] = datetime.utcnow().isoformat()
    supplier.config = config


@router.post("/suppliers/payerpin/test-connection")
async def payerpin_test(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Safe connection test (getBalance/me). NEVER creates a top-up."""
    payerpin = get_payerpin()
    if not payerpin.configured:
        raise HTTPException(status_code=503, detail="not_configured")
    try:
        result = await payerpin.verify_connection()
        _mark_supplier_request(db, True)
        write_audit(db, admin_user_id=admin.id, action="payerpin.test_connection", result="SUCCESS")
        db.commit()
        return {"ok": True, "message": "Connection successful", "account": result.get("account")}
    except SupplierError as exc:
        _mark_supplier_request(db, False)
        write_audit(db, admin_user_id=admin.id, action="payerpin.test_connection",
                    result="FAILED", details={"error": exc.code})
        db.commit()
        raise HTTPException(status_code=502, detail=exc.safe_message)


@router.post("/suppliers/payerpin/check-balance")
async def payerpin_balance(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    payerpin = get_payerpin()
    if not payerpin.configured:
        raise HTTPException(status_code=503, detail="not_configured")
    try:
        result = await payerpin.get_balance()
        supplier = db.execute(select(Supplier).where(Supplier.code == "payerpin")).scalar_one()
        config = dict(supplier.config or {})
        config["balance"] = result.balance
        config["balance_currency"] = result.currency
        config["balance_checked_at"] = datetime.utcnow().isoformat()
        config["low_balance_warning"] = bool(result.balance is not None and result.balance < 500000)
        supplier.config = config
        _mark_supplier_request(db, True)
        write_audit(db, admin_user_id=admin.id, action="payerpin.check_balance", result="SUCCESS")
        db.commit()
        return {
            "balance": result.balance,
            "currency": result.currency,
            "checked_at": config["balance_checked_at"],
            "low_balance_warning": config["low_balance_warning"],
        }
    except SupplierError as exc:
        _mark_supplier_request(db, False)
        db.commit()
        raise HTTPException(status_code=502, detail=exc.safe_message)


@router.post("/suppliers/payerpin/sync-catalog")
async def payerpin_sync(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    payerpin = get_payerpin()
    if not payerpin.configured:
        write_audit(db, admin_user_id=admin.id, action="payerpin.sync_catalog", result="FAILED",
                    details={"error": "NOT_CONFIGURED"})
        db.commit()
        raise HTTPException(status_code=503, detail="not_configured")
    try:
        stats = await sync_payerpin_catalog(db)
        _mark_supplier_request(db, True)
        write_audit(db, admin_user_id=admin.id, action="payerpin.sync_catalog", result="SUCCESS",
                    details=stats)
        db.commit()
        return stats
    except CatalogSyncError as exc:
        _mark_supplier_request(db, False)
        write_audit(db, admin_user_id=admin.id, action="payerpin.sync_catalog", result="FAILED",
                    details={"error": exc.safe_message[:200]})
        db.commit()
        raise HTTPException(status_code=502, detail=exc.safe_message)
    except SupplierError as exc:
        _mark_supplier_request(db, False)
        db.commit()
        raise HTTPException(status_code=502, detail=exc.safe_message)


# ---------------------------------------------------------------------------
# Pricing / coupons / promotions
# ---------------------------------------------------------------------------
@router.get("/pricing")
def get_pricing(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return {"pricing": get_pricing_settings(db)}


@router.put("/pricing")
def put_pricing(
    body: PricingBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    current = get_pricing_settings(db)
    current.update(body.model_dump(exclude_none=True))
    row = db.execute(select(AdminSetting).where(AdminSetting.key == "pricing")).scalar_one()
    row.value = current
    write_audit(db, admin_user_id=admin.id, action="pricing.updated", target_type="settings",
                target_id="pricing", details=body.model_dump(exclude_none=True))
    # recalculate variant prices
    for variant in db.execute(select(ProductVariant)).scalars():
        if variant.supplier_cost is not None:
            breakdown = compute_price(db, variant)
            variant.price_amount = breakdown.unit_price
    db.commit()
    return {"pricing": current}


@router.get("/coupons")
def list_coupons(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.execute(select(Coupon).order_by(Coupon.id.desc())).scalars().all()
    return {
        "items": [
            {
                "id": c.id,
                "code": c.code,
                "coupon_type": c.coupon_type.value,
                "value": c.value,
                "min_order_amount": c.min_order_amount,
                "max_uses": c.max_uses,
                "used_count": c.used_count,
                "per_user_limit": c.per_user_limit,
                "allow_below_margin": c.allow_below_margin,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                "active": c.active,
            }
            for c in rows
        ]
    }


@router.post("/coupons")
def create_coupon(
    body: CouponBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    code = body.code.upper().strip()
    if db.execute(select(Coupon).where(Coupon.code == code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="code_exists")
    try:
        ctype = CouponType(body.coupon_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_type")
    if ctype == CouponType.PERCENT and body.value > 100:
        raise HTTPException(status_code=400, detail="invalid_value")
    coupon = Coupon(
        code=code,
        coupon_type=ctype,
        value=body.value,
        min_order_amount=body.min_order_amount,
        max_uses=body.max_uses,
        per_user_limit=body.per_user_limit,
        allow_below_margin=body.allow_below_margin,
        expires_at=body.expires_at,
        active=body.active,
    )
    db.add(coupon)
    write_audit(db, admin_user_id=admin.id, action="coupon.created", target_type="coupon", target_id=code)
    db.commit()
    return {"ok": True, "id": coupon.id}


@router.patch("/coupons/{coupon_id}")
def patch_coupon(
    coupon_id: int,
    body: CouponBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="coupon_not_found")
    coupon.value = body.value
    coupon.min_order_amount = body.min_order_amount
    coupon.max_uses = body.max_uses
    coupon.per_user_limit = body.per_user_limit
    coupon.allow_below_margin = body.allow_below_margin
    coupon.expires_at = body.expires_at
    coupon.active = body.active
    write_audit(db, admin_user_id=admin.id, action="coupon.updated", target_type="coupon", target_id=coupon.code)
    db.commit()
    return {"ok": True}


@router.get("/promotions")
def list_promotions(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.execute(select(Promotion).order_by(Promotion.id.desc())).scalars().all()
    return {
        "items": [
            {
                "id": p.id,
                "title": p.title,
                "title_uz": p.title_uz,
                "title_ru": p.title_ru,
                "description_en": p.description_en,
                "image_url": p.image_url,
                "coupon_code": p.coupon_code,
                "game_id": p.game_id,
                "starts_at": p.starts_at.isoformat() if p.starts_at else None,
                "ends_at": p.ends_at.isoformat() if p.ends_at else None,
                "active": p.active,
            }
            for p in rows
        ]
    }


@router.post("/promotions")
def create_promotion(
    body: PromotionBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    promo = Promotion(**body.model_dump())
    db.add(promo)
    write_audit(db, admin_user_id=admin.id, action="promotion.created", target_type="promotion",
                target_id=body.title)
    db.commit()
    return {"ok": True, "id": promo.id}


@router.patch("/promotions/{promotion_id}")
def patch_promotion(
    promotion_id: int,
    body: PromotionBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    promo = db.get(Promotion, promotion_id)
    if promo is None:
        raise HTTPException(status_code=404, detail="promotion_not_found")
    for key, value in body.model_dump().items():
        setattr(promo, key, value)
    write_audit(db, admin_user_id=admin.id, action="promotion.updated", target_type="promotion",
                target_id=promo.id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Notifications / telegram / security / audit / settings
# ---------------------------------------------------------------------------
@router.get("/notifications")
def admin_notifications(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Notification).order_by(Notification.id.desc())
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {
        "items": [
            {
                "id": n.id,
                "user_id": n.user_id,
                "kind": n.kind,
                "title_key": n.title_key,
                "body_key": n.body_key,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/notifications/broadcast")
async def broadcast(
    body: BroadcastBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = db.execute(select(User).where(User.is_active == True)).scalars().all()  # noqa: E712
    for user in users:
        notify_user(
            db,
            user,
            kind="admin",
            title_key="admin_message",
            body_key="custom",
            params={"title": body.title, "text": body.text},
        )
    write_audit(db, admin_user_id=admin.id, action="notification.broadcast",
                details={"users": len(users)})
    db.commit()
    return {"ok": True, "sent": len(users)}


@router.get("/telegram")
def telegram_status(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    settings = get_settings()
    rows = db.execute(select(TelegramUser).order_by(TelegramUser.id.desc()).limit(50)).scalars().all()
    return {
        "configured": settings.telegram_configured(),
        "admin_ids_configured": len(settings.telegram_admin_ids),
        "users": [
            {
                "telegram_id": t.telegram_id,
                "username": t.username,
                "first_name": t.first_name,
                "language": t.language,
                "linked": bool(t.user_id),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ],
    }


@router.get("/security")
def security_overview(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    recent_audit = (
        db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(20))
        .scalars()
        .all()
    )
    return {
        "password_hashing": "bcrypt",
        "session_storage": "server-side hashed tokens",
        "rate_limiting": get_settings().rate_limit_enabled,
        "webhook_signatures": "HMAC-SHA256",
        "locked_accounts": db.execute(
            select(func.count(User.id)).where(User.locked_until > datetime.utcnow())
        ).scalar()
        or 0,
        "failed_webhooks": db.execute(
            select(func.count(WebhookEvent.id)).where(WebhookEvent.signature_valid == False)  # noqa: E712
        ).scalar()
        or 0,
        "recent_admin_actions": [
            {
                "action": a.action,
                "target": f"{a.target_type}:{a.target_id}" if a.target_type else None,
                "result": a.result,
                "admin": a.admin_user_id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_audit
        ],
    }


@router.get("/audit-logs")
def audit_logs(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(AuditLog)
    if search:
        query = query.where(AuditLog.action.ilike(f"%{search.strip()}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = (
        db.execute(query.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": a.id,
                "admin_user_id": a.admin_user_id,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "result": a.result,
                "details": a.details,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/settings")
def get_settings_route(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.execute(select(AdminSetting)).scalars().all()
    return {
        "settings": {row.key: row.value for row in rows},
        "env": get_settings().safe_summary(),
    }


@router.put("/settings/{key}")
def put_settings(
    key: str,
    body: SettingsBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if key not in ("general", "pricing"):
        raise HTTPException(status_code=400, detail="invalid_key")
    row = db.execute(select(AdminSetting).where(AdminSetting.key == key)).scalar_one_or_none()
    if row is None:
        row = AdminSetting(key=key, value=body.value)
        db.add(row)
    else:
        row.value = body.value
    write_audit(db, admin_user_id=admin.id, action="settings.updated", target_type="settings",
                target_id=key, details={"keys": list(body.value.keys())})
    db.commit()
    return {"ok": True}

"""Catalog API: games, products, variants, search, promotions (public)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.i18n import normalize_lang
from app.models import Game, Product, ProductVariant, Promotion, Supplier, SupplierTransaction
from datetime import datetime

router = APIRouter(prefix="/api", tags=["catalog"])


def _game_name(game: Game, lang: str) -> str:
    if lang == "uz" and game.name_uz:
        return game.name_uz
    if lang == "ru" and game.name_ru:
        return game.name_ru
    return game.name


def _product_name(product: Product, lang: str) -> str:
    if lang == "uz" and product.name_uz:
        return product.name_uz
    if lang == "ru" and product.name_ru:
        return product.name_ru
    return product.name


def _localized(texts: dict, lang: str) -> str:
    key = f"description_{lang}"
    return texts.get(key) or texts.get("description_en") or ""


def serialize_variant(variant: ProductVariant, price: Optional[int], lang: str) -> dict:
    available = bool(variant.active and variant.in_stock and price is not None)
    name = variant.name
    if lang == "uz" and variant.name_uz:
        name = variant.name_uz
    elif lang == "ru" and variant.name_ru:
        name = variant.name_ru
    return {
        "id": variant.id,
        "name": name,
        "amount_label": variant.amount_label,
        "price": price,
        "currency": variant.price_currency,
        "available": available,
        "region": variant.region,
    }


def serialize_product(
    session: Session, product: Product, lang: str, *, with_variants: bool = True
) -> dict:
    from app.services.pricing import effective_price

    sellable_variants = []
    if with_variants:
        for variant in sorted(product.variants, key=lambda v: (v.sort_order, v.id)):
            price = effective_price(session, variant)
            sellable_variants.append(serialize_variant(variant, price, lang))

    any_available = any(v["available"] for v in sellable_variants) if with_variants else False
    return {
        "id": product.id,
        "slug": product.slug,
        "name": _product_name(product, lang),
        "description": _localized(
            {
                "description_uz": product.description_uz,
                "description_en": product.description_en,
                "description_ru": product.description_ru,
            },
            lang,
        ),
        "product_type": product.product_type.value,
        "fulfillment_type": product.fulfillment_type.value,
        "image_url": product.image_url,
        "required_fields": (product.required_fields or {}).get("fields", []),
        "available": bool(product.active and product.visibility and any_available),
        "active": product.active,
        "variants": sellable_variants,
        "game": {"slug": product.game.slug, "name": _game_name(product.game, lang)}
        if product.game
        else None,
    }


def serialize_game(
    session: Session, game: Game, lang: str, *, with_products: bool = False
) -> dict:
    data = {
        "id": game.id,
        "slug": game.slug,
        "name": _game_name(game, lang),
        "description": _localized(
            {
                "description_uz": game.description_uz,
                "description_en": game.description_en,
                "description_ru": game.description_ru,
            },
            lang,
        ),
        "category": game.category,
        "currency_label": game.currency_label,
        "icon_url": game.icon_url,
        "logo_url": game.logo_url,
        "banner_url": game.banner_url,
        "featured": game.featured,
        "active": game.active,
    }
    if with_products:
        products = [
            serialize_product(session, p, lang)
            for p in game.products
            if p.visibility
        ]
        data["products"] = products
        data["available"] = any(p["available"] for p in products)
        fields: list[dict] = []
        for p in products:
            for f in p["required_fields"]:
                if f not in fields:
                    fields.append(f)
        data["required_fields"] = fields
    return data


@router.get("/config")
def public_config(lang: str = Query(default="uz"), db: Session = Depends(get_db)):
    """Public, secret-free site configuration (branding is admin-editable)."""
    from app.config import get_settings
    from app.services.site_settings import get_branding

    settings = get_settings()
    lang = normalize_lang(lang)
    branding = get_branding(db)
    return {
        "brand": branding.get("site_name") or "VYRON",
        "branding": branding,
        "languages": ["uz", "en", "ru"],
        "payment_configured": settings.payment_configured(),
        "topup_configured": settings.hamyon_configured(),
        "provider_payment_configured": settings.provider_payment_configured(),
        "payment_provider": settings.payment_provider or None,
        "supplier_configured": settings.payerpin_configured(),
        "telegram_configured": settings.telegram_configured(),
        "currency": settings.payment_currency,
    }


@router.get("/games")
def list_games(
    db: Session = Depends(get_db),
    lang: str = Query(default="uz"),
    search: Optional[str] = Query(default=None, max_length=64),
    category: Optional[str] = Query(default=None, max_length=64),
    featured: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
):
    lang = normalize_lang(lang)
    query = select(Game).where(Game.active == True)  # noqa: E712
    if category:
        query = query.where(Game.category == category)
    if featured is not None:
        query = query.where(Game.featured == featured)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            or_(Game.name.ilike(like), Game.name_uz.ilike(like), Game.name_ru.ilike(like))
        )
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    rows = (
        db.execute(
            query.order_by(Game.sort_order, Game.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = []
    for game in rows:
        has_available = db.execute(
            select(func.count(ProductVariant.id))
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                Product.game_id == game.id,
                Product.active == True,  # noqa: E712
                Product.visibility == True,  # noqa: E712
                ProductVariant.active == True,  # noqa: E712
                ProductVariant.in_stock == True,  # noqa: E712
                ProductVariant.supplier_cost.is_not(None),
            )
        ).scalar() or 0
        data = serialize_game(db, game, lang)
        data["available"] = bool(has_available)
        items.append(data)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/games/{slug}")
def get_game(slug: str, db: Session = Depends(get_db), lang: str = Query(default="uz")):
    lang = normalize_lang(lang)
    game = db.execute(select(Game).where(Game.slug == slug, Game.active == True)).scalar_one_or_none()  # noqa: E712
    if game is None:
        raise HTTPException(status_code=404, detail="game_not_found")
    return {"game": serialize_game(db, game, lang, with_products=True)}


@router.get("/products")
def list_products(
    db: Session = Depends(get_db),
    lang: str = Query(default="uz"),
    search: Optional[str] = Query(default=None, max_length=64),
    game: Optional[str] = Query(default=None, max_length=96),
    available_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    lang = normalize_lang(lang)
    query = (
        select(Product)
        .join(Game, Game.id == Product.game_id)
        .where(Product.visibility == True, Game.active == True)  # noqa: E712
    )
    if game:
        query = query.where(Game.slug == game)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            or_(Product.name.ilike(like), Product.slug.ilike(like), Game.name.ilike(like))
        )
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    rows = (
        db.execute(query.order_by(Product.id).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    items = [serialize_product(db, p, lang) for p in rows]
    if available_only:
        items = [p for p in items if p["available"]]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db), lang: str = Query(default="uz")):
    lang = normalize_lang(lang)
    product = db.get(Product, product_id)
    if product is None or not product.visibility:
        raise HTTPException(status_code=404, detail="product_not_found")
    return {"product": serialize_product(db, product, lang)}


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=64), db: Session = Depends(get_db),
           lang: str = Query(default="uz")):
    lang = normalize_lang(lang)
    like = f"%{q.strip()}%"
    games = (
        db.execute(
            select(Game)
            .where(
                Game.active == True,  # noqa: E712
                or_(Game.name.ilike(like), Game.name_uz.ilike(like), Game.name_ru.ilike(like)),
            )
            .limit(10)
        )
        .scalars()
        .all()
    )
    products = (
        db.execute(
            select(Product)
            .where(
                Product.visibility == True,  # noqa: E712
                or_(Product.name.ilike(like), Product.slug.ilike(like)),
            )
            .limit(10)
        )
        .scalars()
        .all()
    )
    return {
        "games": [serialize_game(db, g, lang) for g in games],
        "products": [serialize_product(db, p, lang, with_variants=False) for p in products],
    }


@router.get("/promotions")
def promotions(db: Session = Depends(get_db), lang: str = Query(default="uz")):
    lang = normalize_lang(lang)
    now = datetime.utcnow()
    rows = (
        db.execute(select(Promotion).where(Promotion.active == True).order_by(Promotion.id.desc()))  # noqa: E712
        .scalars()
        .all()
    )
    items = []
    for promo in rows:
        if promo.starts_at and promo.starts_at > now:
            continue
        if promo.ends_at and promo.ends_at < now:
            continue
        title = promo.title
        if lang == "uz" and promo.title_uz:
            title = promo.title_uz
        elif lang == "ru" and promo.title_ru:
            title = promo.title_ru
        items.append(
            {
                "id": promo.id,
                "title": title,
                "description": _localized(
                    {
                        "description_uz": promo.description_uz,
                        "description_en": promo.description_en,
                        "description_ru": promo.description_ru,
                    },
                    lang,
                ),
                "image_url": promo.image_url,
                "coupon_code": promo.coupon_code,
                "game_id": promo.game_id,
            }
        )
    return {"items": items}


@router.get("/supplier-status")
def supplier_status(db: Session = Depends(get_db)):
    """Public honesty banner data: is automated fulfillment actually available?"""
    supplier = db.execute(select(Supplier).where(Supplier.code == "payerpin")).scalar_one_or_none()
    from app.config import get_settings

    configured = get_settings().payerpin_configured()
    active_products = 0
    if supplier is not None:
        active_products = db.execute(
            select(func.count(ProductVariant.id)).where(
                ProductVariant.active == True,  # noqa: E712
                ProductVariant.supplier_cost.is_not(None),
            )
        ).scalar() or 0
    return {
        "automated_fulfillment": configured and active_products > 0,
        "supplier_configured": configured,
        "active_products": active_products,
    }


@router.get("/art/currency.svg", include_in_schema=False)
def currency_art(c: str = Query(default="", max_length=32)):
    """Generated fallback artwork for games without a shipped/uploaded image."""
    from fastapi.responses import Response

    from app.migrations import currency_svg

    return Response(
        content=currency_svg(c),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400",
                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )

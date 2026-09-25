"""Real Payerpin catalog synchronization. NEVER invents supplier IDs/prices."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import (
    CatalogSyncLog,
    FulfillmentType,
    Game,
    Product,
    ProductType,
    ProductVariant,
    Supplier,
    SupplierProduct,
)
from app.providers.suppliers.base import SupplierError
from app.providers.suppliers.payerpin import get_payerpin
from app.services.pricing import compute_price

log = get_logger("vyron.catalog_sync")


class CatalogSyncError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.safe_message = message


def _to_minor_units(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _extract_entries(catalog: dict) -> list[dict]:
    """Normalize a Payerpin catalog payload into product entries.

    Accepts documented shapes: {"data": {"games": [...]}} or {"games": [...]}
    or a list of games, each with products, each with variations.
    """
    root = catalog.get("data", catalog) if isinstance(catalog, dict) else catalog
    games = []
    if isinstance(root, dict):
        games = root.get("games") or root.get("catalog") or root.get("items") or []
    elif isinstance(root, list):
        games = root
    entries: list[dict] = []
    for game in games or []:
        if not isinstance(game, dict):
            continue
        game_key = str(game.get("key") or game.get("id") or game.get("gameKey") or "")
        game_name = game.get("name") or game.get("title") or game_key
        products = game.get("products") or game.get("items") or []
        for product in products or []:
            if not isinstance(product, dict):
                continue
            product_id = product.get("id") or product.get("productId")
            variations = product.get("variations") or product.get("packages") or [None]
            for variation in variations:
                if variation is not None and not isinstance(variation, dict):
                    continue
                variation_id = (
                    (variation or {}).get("id")
                    or (variation or {}).get("variationId")
                    or (variation or {}).get("packageId")
                )
                entries.append(
                    {
                        "game_key": game_key,
                        "game_name": game_name,
                        "product_id": product_id,
                        "product_name": product.get("name") or product.get("title") or "",
                        "variation_id": variation_id,
                        "variation_name": (variation or {}).get("name") or (variation or {}).get("title"),
                        "price": (variation or {}).get("price", product.get("price")),
                        "currency": (variation or {}).get("currency", product.get("currency")),
                        "region": (variation or {}).get("region", product.get("region")),
                        "required_fields": product.get("required_fields")
                        or product.get("fields")
                        or (variation or {}).get("fields"),
                        "fulfillment_type": product.get("type") or product.get("fulfillment_type"),
                        "raw": {"product": product, "variation": variation},
                    }
                )
    return entries


async def sync_payerpin_catalog(session: Session) -> dict:
    """Pull the REAL Payerpin catalog and map it onto VYRON products.

    - Preserves historical orders (never deletes transactions).
    - Activates only products actually provided by the supplier.
    - Products without supplier data stay inactive / NOT AVAILABLE.
    """
    payerpin = get_payerpin()
    supplier = session.execute(
        select(Supplier).where(Supplier.code == "payerpin")
    ).scalar_one_or_none()
    if supplier is None:
        raise CatalogSyncError("Supplier record missing")

    sync_log = CatalogSyncLog(supplier_id=supplier.id, status="STARTED")
    session.add(sync_log)
    session.flush()

    if not payerpin.configured:
        sync_log.status = "NOT_CONFIGURED"
        sync_log.error = "PAYERPIN_API_KEY NOT CONFIGURED"
        sync_log.finished_at = datetime.utcnow()
        session.commit()  # persist the failure log before raising
        raise CatalogSyncError("Payerpin NOT CONFIGURED")

    try:
        catalog = await payerpin.get_catalog()
    except SupplierError as exc:
        sync_log.status = "FAILED"
        sync_log.error = exc.safe_message[:500]
        sync_log.finished_at = datetime.utcnow()
        session.commit()  # persist the failure log before raising
        raise CatalogSyncError(exc.safe_message) from exc

    entries = _extract_entries(catalog)
    from app.services.site_settings import get_rates

    rates = get_rates(session)
    needs_rate: set[str] = set()
    games_found: set[str] = set()
    product_ids_found: set[str] = set()
    variants_found = 0
    activated = 0
    deactivated = 0

    seen_variant_keys: set[tuple] = set()

    for entry in entries:
        if not entry["product_id"]:
            continue  # never invent supplier product IDs
        product_ids_found.add(str(entry["product_id"]))
        games_found.add(entry["game_key"])
        product_id_str = str(entry["product_id"])
        variation_id_str = str(entry["variation_id"]) if entry["variation_id"] is not None else None
        key = (product_id_str, variation_id_str)
        seen_variant_keys.add(key)

        cost = _to_minor_units(entry["price"])
        sp = session.execute(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier.id,
                SupplierProduct.supplier_product_id == product_id_str,
                SupplierProduct.supplier_variation_id == (variation_id_str or ""),
            )
        ).scalar_one_or_none()
        if sp is None:
            sp = SupplierProduct(
                supplier_id=supplier.id,
                game_key=entry["game_key"],
                game_name=entry["game_name"],
                supplier_product_id=product_id_str,
                supplier_variation_id=variation_id_str or "",
                name=entry["variation_name"] or entry["product_name"] or "",
            )
            session.add(sp)
        sp.game_name = entry["game_name"]
        sp.name = entry["variation_name"] or entry["product_name"] or sp.name
        sp.supplier_cost = cost
        sp.currency = entry["currency"]
        sp.region = entry["region"]
        sp.required_fields = _normalize_fields(entry["required_fields"])
        sp.fulfillment_type = _map_fulfillment_type(entry["fulfillment_type"])
        sp.raw = entry["raw"]
        sp.active = True
        sp.last_seen_at = datetime.utcnow()
        session.flush()

        # map to a VYRON game (by supplier key or slug/name match); games the
        # supplier offers that are not in the seed catalog are created automatically
        game = _match_game(session, entry) or _create_game(session, entry)
        if game is None:
            continue
        uzs_cost = _convert_to_uzs(cost, entry["currency"], rates)
        if cost is not None and uzs_cost is None:
            needs_rate.add(str(entry["currency"]).upper())
        if not game.supplier_game_key:
            game.supplier_game_key = entry["game_key"]

        # map to VYRON product + variant
        product = _match_product(session, game, entry)
        variant = _match_variant(session, product, sp, entry)
        variant.supplier_cost = uzs_cost  # customer pricing is always in UZS
        sp.product_id = product.id
        sp.variant_id = variant.id
        if uzs_cost is None:
            # real supplier product, but no usable price yet (missing FX rate):
            # stays NOT AVAILABLE until the admin configures the rate.
            variant.active = False
            variant.in_stock = False
            variants_found += 1
            continue
        breakdown = compute_price(session, variant)
        variant.price_amount = breakdown.unit_price
        if not variant.active:
            activated += 1
        variant.active = True
        variant.in_stock = True
        if not product.active:
            product.active = True

        variants_found += 1

    # deactivate supplier products not seen in this sync
    all_sp = (
        session.execute(
            select(SupplierProduct).where(SupplierProduct.supplier_id == supplier.id)
        )
        .scalars()
        .all()
    )
    for sp in all_sp:
        key = (sp.supplier_product_id, sp.supplier_variation_id or None)
        if key not in seen_variant_keys and (sp.supplier_product_id, sp.supplier_variation_id or "") not in {
            (a, b or "") for a, b in seen_variant_keys
        }:
            if sp.active:
                sp.active = False
                deactivated += 1
            if sp.variant_id:
                variant = session.get(ProductVariant, sp.variant_id)
                if variant and variant.active:
                    variant.active = False
                    variant.in_stock = False

    products_found = len(product_ids_found)
    sync_log.status = "SUCCESS"
    sync_log.games_found = len(games_found)
    sync_log.products_found = products_found
    sync_log.variants_found = variants_found
    sync_log.products_activated = activated
    sync_log.products_deactivated = deactivated
    sync_log.finished_at = datetime.utcnow()

    supplier.config = dict(supplier.config or {})
    supplier.config["last_catalog_sync"] = datetime.utcnow().isoformat()
    supplier.config["last_sync_stats"] = {
        "games": len(games_found),
        "products": products_found,
        "variants": variants_found,
    }
    log.info(
        "catalog sync complete games=%s products=%s variants=%s",
        len(games_found), products_found, variants_found,
    )
    if needs_rate:
        supplier.config["currencies_missing_rate"] = sorted(needs_rate)
    return {
        "status": "SUCCESS",
        "currencies_missing_rate": sorted(needs_rate),
        "games_found": len(games_found),
        "products_found": products_found,
        "variants_found": variants_found,
        "products_activated": activated,
        "products_deactivated": deactivated,
    }


def _convert_to_uzs(cost_minor: Optional[int], currency: Any, rates: dict) -> Optional[int]:
    """Supplier cost (minor units, supplier currency) -> UZS minor units."""
    if cost_minor is None:
        return None
    code = str(currency or "UZS").upper().strip() or "UZS"
    if code in ("UZS", "SUM", "SO'M", "SOM"):
        return int(cost_minor)
    rate = rates.get(code)
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    return int(round(cost_minor * rate))


def _slugify(text: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:80] or "game"


def _create_game(session: Session, entry: dict) -> Optional[Game]:
    name = (entry.get("game_name") or entry.get("game_key") or "").strip()
    if not name:
        return None
    base = _slugify(entry.get("game_key") or name)
    slug, i = base, 1
    while session.execute(select(Game).where(Game.slug == slug)).scalar_one_or_none():
        slug = f"{base}-{i}"
        i += 1
    from app.migrations import _currency_art

    art = _currency_art("")
    game = Game(
        slug=slug,
        name=name[:128],
        category="other",
        currency_label="",
        icon_url=art,
        logo_url=art,
        banner_url=art,
        supplier_game_key=entry.get("game_key") or None,
        sort_order=500,
        featured=False,
        active=True,
    )
    session.add(game)
    session.flush()
    log.info("catalog sync: created game for supplier key=%s", entry.get("game_key"))
    return game


def _normalize_fields(raw: Any) -> dict:
    if not raw:
        return {"fields": []}
    if isinstance(raw, dict) and "fields" in raw:
        return raw
    fields = []
    if isinstance(raw, list):
        for f in raw:
            if isinstance(f, dict):
                fields.append(
                    {
                        "key": f.get("key") or f.get("name") or f.get("id"),
                        "label": f.get("label") or f.get("name") or f.get("key"),
                        "type": f.get("type", "string"),
                        "required": bool(f.get("required", True)),
                    }
                )
    elif isinstance(raw, dict):
        for k, v in raw.items():
            fields.append({"key": k, "label": k, "type": str(v), "required": True})
    return {"fields": [f for f in fields if f.get("key")]}


def _map_fulfillment_type(raw: Any) -> FulfillmentType:
    text = str(raw or "").lower()
    if "manual" in text:
        return FulfillmentType.MANUAL
    return FulfillmentType.AUTO


def _match_game(session: Session, entry: dict) -> Optional[Game]:
    game_key = entry["game_key"]
    game = session.execute(
        select(Game).where(Game.supplier_game_key == game_key)
    ).scalar_one_or_none()
    if game is not None:
        return game
    slug = game_key.lower().replace("_", "-").replace(" ", "-")
    game = session.execute(select(Game).where(Game.slug == slug)).scalar_one_or_none()
    if game is not None:
        return game
    name = (entry["game_name"] or "").strip().lower()
    for candidate in session.execute(select(Game)).scalars():
        names = {candidate.name.lower()}
        if candidate.name_uz:
            names.add(candidate.name_uz.lower())
        if candidate.name_ru:
            names.add(candidate.name_ru.lower())
        if name in names:
            return candidate
    return None


def _match_product(session: Session, game: Game, entry: dict) -> Product:
    product = session.execute(
        select(Product).where(Product.game_id == game.id)
    ).scalars().first()
    if product is not None:
        if entry.get("required_fields") and product.required_fields != entry["required_fields"]:
            product.required_fields = _normalize_fields(entry["required_fields"])
        return product
    product = Product(
        game_id=game.id,
        slug=f"{game.slug}-auto-{entry['product_id']}",
        name=entry["product_name"] or game.name,
        product_type=ProductType.TOPUP,
        fulfillment_type=_map_fulfillment_type(entry.get("fulfillment_type")),
        image_url=game.icon_url,
        required_fields=_normalize_fields(entry.get("required_fields")),
        active=True,
    )
    session.add(product)
    session.flush()
    return product


def _match_variant(
    session: Session, product: Product, sp: SupplierProduct, entry: dict
) -> ProductVariant:
    variant = session.execute(
        select(ProductVariant).where(
            ProductVariant.product_id == product.id,
            ProductVariant.supplier_product_id == sp.supplier_product_id,
            ProductVariant.supplier_variation_id == (sp.supplier_variation_id or None),
        )
    ).scalars().first()
    if variant is not None:
        variant.supplier_cost = sp.supplier_cost
        return variant
    name = sp.name or entry.get("variation_name") or sp.supplier_product_id
    variant = ProductVariant(
        product_id=product.id,
        name=str(name),
        amount_label=str(name),
        supplier_product_id=sp.supplier_product_id,
        supplier_variation_id=sp.supplier_variation_id or None,
        supplier_cost=sp.supplier_cost,
        supplier_currency=sp.currency,
        region=sp.region,
        active=False,
    )
    session.add(variant)
    session.flush()
    return variant

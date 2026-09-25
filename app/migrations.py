"""Versioned schema migrations + seed data. Runs automatically at startup."""
from __future__ import annotations

from sqlalchemy import select, text

from app.db import Base, get_engine, session_scope
from app.logging_config import get_logger
from app.models import (
    AdminSetting,
    CouponCode,
    FulfillmentType,
    Game,
    Product,
    ProductType,
    ProductVariant,
    Role,
    RoleName,
    Supplier,
)

log = get_logger("vyron.migrations")

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INT NOT NULL PRIMARY KEY,"
        " applied_at DATETIME NOT NULL)",
    ),
]


# Columns added after the first release: (table, column, DDL type/default).
# create_all() does not ALTER existing tables, so add them idempotently.
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "balance", "BIGINT NOT NULL DEFAULT 0"),
]


def _ensure_columns(engine) -> None:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in ADDED_COLUMNS:
            if table not in existing_tables:
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                log.info("migration: added column %s.%s", table, column)


def run_migrations() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    with session_scope() as session:
        # bootstrap the version table first (idempotent)
        for stmt in MIGRATIONS[0][1].split(";"):
            if stmt.strip():
                session.execute(text(stmt))
        for version, sql in MIGRATIONS:
            exists = session.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = :v"),
                {"v": version},
            ).scalar()
            if not exists:
                for stmt in sql.split(";"):
                    if stmt.strip():
                        session.execute(text(stmt))
                session.execute(
                    text("INSERT INTO schema_migrations (version, applied_at) "
                         "VALUES (:v, CURRENT_TIMESTAMP)"),
                    {"v": version},
                )
        log.info("Migrations applied (schema version %s)", max(v for v, _ in MIGRATIONS))


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
# Desired VYRON catalog structure (per master spec §9). These are GAME
# records only — no prices, no supplier IDs. Products/variants become
# purchasable ONLY after a real supplier (Payerpin) catalog sync maps them.
GAMES_SEED: list[dict] = [
    # slug, name, uz, ru, category, currency_label
    ("pubg-mobile", "PUBG Mobile", "PUBG Mobile", "PUBG Mobile", "mobile", "UC"),
    ("roblox", "Roblox", "Roblox", "Roblox", "mobile", "Robux"),
    ("clash-of-clans", "Clash of Clans", "Clash of Clans", "Clash of Clans", "mobile", "Gems"),
    ("clash-royale", "Clash Royale", "Clash Royale", "Clash Royale", "mobile", "Gems"),
    ("standoff-2", "Standoff 2", "Standoff 2", "Standoff 2", "mobile", "Gold"),
    ("free-fire", "Free Fire", "Free Fire", "Free Fire", "mobile", "Diamonds"),
    ("free-fire-max", "Free Fire MAX", "Free Fire MAX", "Free Fire MAX", "mobile", "Diamonds"),
    ("mobile-legends", "Mobile Legends: Bang Bang", "Mobile Legends: Bang Bang",
     "Mobile Legends: Bang Bang", "mobile", "Diamonds"),
    ("honor-of-kings", "Honor of Kings", "Honor of Kings", "Honor of Kings", "mobile", "Tokens"),
    ("league-of-legends", "League of Legends", "League of Legends", "League of Legends",
     "pc", "RP"),
    ("valorant", "VALORANT", "VALORANT", "VALORANT", "pc", "VP"),
    ("counter-strike-2", "Counter-Strike 2", "Counter-Strike 2", "Counter-Strike 2", "pc", "Credits"),
    ("dota-2", "Dota 2", "Dota 2", "Dota 2", "pc", "Shards"),
    ("ea-sports-fc", "EA SPORTS FC", "EA SPORTS FC", "EA SPORTS FC", "console", "FC Points"),
    ("genshin-impact", "Genshin Impact", "Genshin Impact", "Genshin Impact", "mobile", "Primogems"),
    ("honkai-star-rail", "Honkai: Star Rail", "Honkai: Star Rail", "Honkai: Star Rail", "mobile",
     "Oneiric Shards"),
    ("zenless-zone-zero", "Zenless Zone Zero", "Zenless Zone Zero", "Zenless Zone Zero", "mobile",
     "Polychrome"),
    ("wuthering-waves", "Wuthering Waves", "Wuthering Waves", "Wuthering Waves", "mobile",
     "Lunite"),
    ("cod-mobile", "Call of Duty: Mobile", "Call of Duty: Mobile", "Call of Duty: Mobile",
     "mobile", "CP"),
    ("cod-warzone", "Call of Duty: Warzone", "Call of Duty: Warzone", "Call of Duty: Warzone",
     "pc", "CP"),
    ("arena-breakout", "Arena Breakout", "Arena Breakout", "Arena Breakout", "mobile", "Bonds"),
    ("pubg-battlegrounds", "PUBG: BATTLEGROUNDS", "PUBG: BATTLEGROUNDS", "PUBG: BATTLEGROUNDS",
     "pc", "G-Coins"),
    ("minecraft", "Minecraft", "Minecraft", "Minecraft", "pc", "Minecoins"),
    ("tlauncher", "TLauncher Products", "TLauncher mahsulotlari", "Продукты TLauncher", "pc",
     "License"),
    ("brawl-stars", "Brawl Stars", "Brawl Stars", "Brawl Stars", "mobile", "Gems"),
    ("pokemon-go", "Pokémon GO", "Pokémon GO", "Pokémon GO", "mobile", "PokéCoins"),
    ("efootball", "eFootball", "eFootball", "eFootball", "console", "Coins"),
    ("asphalt", "Asphalt", "Asphalt", "Asphalt", "mobile", "Tokens"),
    ("xbox", "Xbox", "Xbox", "Xbox", "gift-cards", "USD"),
    ("playstation", "PlayStation", "PlayStation", "PlayStation", "gift-cards", "USD"),
    ("nintendo", "Nintendo", "Nintendo", "Nintendo", "gift-cards", "USD"),
    ("google-play", "Google Play", "Google Play", "Google Play", "gift-cards", "USD"),
    ("apple-gift-card", "Apple Gift Card", "Apple Gift Card", "Apple Gift Card", "gift-cards",
     "USD"),
    ("discord", "Discord", "Discord", "Discord", "digital", "Nitro"),
    ("twitch", "Twitch", "Twitch", "Twitch", "digital", "Bits"),
    ("battlenet", "Battle.net", "Battle.net", "Battle.net", "gift-cards", "USD"),
    ("steam", "Steam", "Steam", "Steam", "gift-cards", "USD"),
    ("telegram-stars", "Telegram Stars", "Telegram Stars", "Telegram Stars", "digital", "Stars"),
    ("gift-cards", "Gift Cards", "Sovg'a kartalari", "Подарочные карты", "gift-cards", "USD"),
    ("other-products", "Other Products", "Boshqa mahsulotlar", "Другие продукты", "digital", ""),
]

FEATURED_SLUGS = {
    "pubg-mobile", "free-fire", "mobile-legends", "roblox",
    "standoff-2", "clash-of-clans", "genshin-impact", "steam",
}

CURRENCY_STYLES: dict[str, dict] = {
    # currency_label -> gradient + glyph used for generated currency art
    "UC": {"a": "#1D4ED8", "b": "#60A5FA", "g": "UC"},
    "Robux": {"a": "#E11D48", "b": "#FB7185", "g": "R$"},
    "Gems": {"a": "#7C3AED", "b": "#C084FC", "g": "◆"},
    "Gold": {"a": "#B45309", "b": "#FBBF24", "g": "Au"},
    "Diamonds": {"a": "#0E7490", "b": "#67E8F9", "g": "◆"},
    "Tokens": {"a": "#0F766E", "b": "#5EEAD4", "g": "✦"},
    "RP": {"a": "#B91C1C", "b": "#F87171", "g": "RP"},
    "VP": {"a": "#9F1239", "b": "#FB7185", "g": "VP"},
    "Credits": {"a": "#334155", "b": "#94A3B8", "g": "CR"},
    "Shards": {"a": "#3730A3", "b": "#818CF8", "g": "◈"},
    "FC Points": {"a": "#065F46", "b": "#34D399", "g": "FC"},
    "Primogems": {"a": "#4338CA", "b": "#A5B4FC", "g": "✦"},
    "Oneiric Shards": {"a": "#5B21B6", "b": "#C4B5FD", "g": "◇"},
    "Polychrome": {"a": "#9A3412", "b": "#FDBA74", "g": "⬡"},
    "Lunite": {"a": "#1E3A8A", "b": "#93C5FD", "g": "☾"},
    "CP": {"a": "#166534", "b": "#86EFAC", "g": "CP"},
    "Bonds": {"a": "#78350F", "b": "#FCD34D", "g": "▣"},
    "G-Coins": {"a": "#0F172A", "b": "#64748B", "g": "G"},
    "Minecoins": {"a": "#166534", "b": "#4ADE80", "g": "⛏"},
    "License": {"a": "#0C4A6E", "b": "#38BDF8", "g": "TL"},
    "PokéCoins": {"a": "#1D4ED8", "b": "#FBBF24", "g": "₽"},  # coin glyph replaced below
    "Coins": {"a": "#A16207", "b": "#FDE047", "g": "◉"},
    "USD": {"a": "#064E3B", "b": "#6EE7B7", "g": "$"},
    "Nitro": {"a": "#4C1D95", "b": "#A78BFA", "g": "N"},
    "Bits": {"a": "#3730A3", "b": "#8B5CF6", "g": "♦"},
    "Stars": {"a": "#0369A1", "b": "#7DD3FC", "g": "★"},
    "": {"a": "#0B2447", "b": "#4DA8DA", "g": "V"},
}


# Professional currency artwork shipped with the app (web/assets/img/currency).
# Each image shows ONLY the game's currency. Admins can replace any of them.
GAME_IMAGES: dict[str, str] = {
    "pubg-mobile": "/assets/img/currency/uc.webp",
    "free-fire": "/assets/img/currency/ff-diamonds.webp",
    "free-fire-max": "/assets/img/currency/ff-diamonds.webp",
    "mobile-legends": "/assets/img/currency/ml-diamonds.webp",
    "roblox": "/assets/img/currency/robux.webp",
    "clash-of-clans": "/assets/img/currency/coc-gems.webp",
    "clash-royale": "/assets/img/currency/cr-gems.webp",
    "brawl-stars": "/assets/img/currency/bs-gems.webp",
    "standoff-2": "/assets/img/currency/so2-gold.webp",
    "genshin-impact": "/assets/img/currency/primogems.webp",
    "telegram-stars": "/assets/img/currency/tg-stars.webp",
    "valorant": "/assets/img/currency/valorant.webp",
    "honor-of-kings": "/assets/img/currency/honor-of-kings.webp",
    "league-of-legends": "/assets/img/currency/league-of-legends.webp",
    "counter-strike-2": "/assets/img/currency/counter-strike-2.webp",
    "dota-2": "/assets/img/currency/dota-2.webp",
    "ea-sports-fc": "/assets/img/currency/ea-sports-fc.webp",
    "honkai-star-rail": "/assets/img/currency/honkai-star-rail.webp",
    "zenless-zone-zero": "/assets/img/currency/zenless-zone-zero.webp",
    "wuthering-waves": "/assets/img/currency/wuthering-waves.webp",
    "cod-mobile": "/assets/img/currency/cod-mobile.webp",
}


def game_image(slug: str, currency_label: str) -> str:
    return GAME_IMAGES.get(slug) or _currency_art(currency_label)


def _currency_art(currency_label: str) -> str:
    """Short URL of the generated fallback currency artwork (served by the API)."""
    from urllib.parse import quote

    return f"/api/art/currency.svg?c={quote(currency_label or '')}"


def currency_svg(currency_label: str) -> str:
    """Fallback SVG showing ONLY the game currency as a soft 3D coin."""
    from xml.sax.saxutils import escape

    style = CURRENCY_STYLES.get(currency_label, CURRENCY_STYLES[""])
    glyph = style["g"] if currency_label not in ("UC", "RP", "VP", "CP", "FC Points") else (
        currency_label.split()[0][:2]
    )
    label = escape(currency_label or "Digital")
    glyph = escape(glyph)
    a, b = style["a"], style["b"]
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512' viewBox='0 0 512 512'>"
        "<defs>"
        "<radialGradient id='bg' cx='0.5' cy='0.35' r='0.8'>"
        "<stop offset='0' stop-color='#1B3F77'/><stop offset='1' stop-color='#081A36'/></radialGradient>"
        f"<linearGradient id='c' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{b}'/>"
        f"<stop offset='1' stop-color='{a}'/></linearGradient>"
        "<radialGradient id='hl' cx='0.35' cy='0.3' r='0.6'><stop offset='0' stop-color='#fff' stop-opacity='.55'/>"
        "<stop offset='1' stop-color='#fff' stop-opacity='0'/></radialGradient>"
        "<filter id='sh' x='-30%' y='-30%' width='160%' height='160%'>"
        "<feDropShadow dx='0' dy='18' stdDeviation='18' flood-color='#000' flood-opacity='.45'/></filter>"
        "</defs>"
        "<rect width='512' height='512' fill='url(#bg)'/>"
        f"<circle cx='256' cy='236' r='170' fill='{b}' opacity='.10'/>"
        "<g filter='url(#sh)'>"
        f"<circle cx='256' cy='236' r='128' fill='{a}'/>"
        "<circle cx='256' cy='228' r='128' fill='url(#c)'/>"
        "<circle cx='256' cy='228' r='104' fill='none' stroke='#fff' stroke-opacity='.35' stroke-width='6'/>"
        "<circle cx='256' cy='228' r='128' fill='url(#hl)'/>"
        "</g>"
        "<text x='256' y='252' font-family='Segoe UI,Arial,sans-serif' font-size='76' font-weight='800' "
        f"fill='#fff' text-anchor='middle'>{glyph}</text>"
        "<text x='256' y='440' font-family='Segoe UI,Arial,sans-serif' font-size='34' font-weight='700' "
        f"fill='#fff' fill-opacity='.9' text-anchor='middle'>{label}</text>"
        "</svg>"
    )
    return svg


def seed_database() -> None:
    with session_scope() as session:
        # roles
        for role_name, label in (
            (RoleName.ADMIN, "Administrator"),
            (RoleName.SUPPORT, "Support"),
            (RoleName.CUSTOMER, "Customer"),
        ):
            if not session.execute(
                select(Role).where(Role.name == role_name)
            ).scalar_one_or_none():
                session.add(Role(name=role_name, label=label))

        # supplier: payerpin
        if not session.execute(
            select(Supplier).where(Supplier.code == "payerpin")
        ).scalar_one_or_none():
            session.add(
                Supplier(
                    code="payerpin",
                    name="Payerpin",
                    config={"base_url": "https://api.payerpin.uz", "api_version": "v2"},
                )
            )

        # default pricing settings (public schema, no secrets)
        if not session.execute(
            select(AdminSetting).where(AdminSetting.key == "pricing")
        ).scalar_one_or_none():
            session.add(
                AdminSetting(
                    key="pricing",
                    value={
                        "margin_percent": 10,
                        "margin_fixed": 0,
                        "payment_fee_percent": 0,
                        "payment_fee_fixed": 0,
                        "min_margin_percent": 0,
                        "allow_below_min_margin": False,
                    },
                )
            )
        if not session.execute(
            select(AdminSetting).where(AdminSetting.key == "general")
        ).scalar_one_or_none():
            session.add(
                AdminSetting(
                    key="general",
                    value={
                        "support_link": "https://t.me/vyron_support",
                        "announcement_uz": "",
                        "announcement_en": "",
                        "announcement_ru": "",
                    },
                )
            )

        # games catalog structure (NOT purchasable until supplier sync)
        existing = {
            g.slug for g in session.execute(select(Game)).scalars().all()
        }
        for i, (slug, name, name_uz, name_ru, category, cur) in enumerate(GAMES_SEED):
            if slug in existing:
                continue
            art = game_image(slug, cur)
            game = Game(
                slug=slug,
                name=name,
                name_uz=name_uz,
                name_ru=name_ru,
                category=category,
                currency_label=cur,
                icon_url=art,
                logo_url=art,
                banner_url=art,
                sort_order=i,
                featured=slug in FEATURED_SLUGS,
                active=True,
            )
            session.add(game)
            session.flush()
            # Inactive product shell: NOT purchasable, NOT priced — awaits
            # real Payerpin catalog sync (never invent supplier IDs / prices).
            product = Product(
                game_id=game.id,
                slug=f"{slug}-topup",
                name=f"{name} Top-up" if category != "gift-cards" else f"{name} Gift Card",
                product_type=(
                    ProductType.GIFT_CARD if category == "gift-cards"
                    else ProductType.TOPUP if category in ("mobile", "pc", "console")
                    else ProductType.DIGITAL
                ),
                fulfillment_type=FulfillmentType.AUTO,
                image_url=art,
                required_fields={"fields": []},
                active=False,
                visibility=True,
            )
            session.add(product)

        # upgrade generated placeholder art to the shipped currency images
        # (never overwrites an image an admin has set)
        for game in session.execute(select(Game)).scalars().all():
            real = GAME_IMAGES.get(game.slug)
            if not real:
                for attr in ("icon_url", "logo_url", "banner_url"):
                    if (getattr(game, attr) or "").startswith("data:"):
                        setattr(game, attr, _currency_art(game.currency_label or ""))
                continue
            for attr in ("icon_url", "logo_url", "banner_url"):
                current = getattr(game, attr)
                if not current or current.startswith("data:") or current.startswith("/api/art/"):
                    setattr(game, attr, real)
            for product in game.products:
                if not product.image_url or product.image_url.startswith(("data:", "/api/art/")):
                    product.image_url = real

        # admin-editable settings with safe defaults (no secrets)
        from app.services.site_settings import DEFAULT_BRANDING, DEFAULT_RATES
        from app.services.wallet import DEFAULT_TOPUP_SETTINGS

        for key, default in (("branding", DEFAULT_BRANDING), ("topup", DEFAULT_TOPUP_SETTINGS),
                             ("rates", DEFAULT_RATES)):
            if not session.execute(
                select(AdminSetting).where(AdminSetting.key == key)
            ).scalar_one_or_none():
                session.add(AdminSetting(key=key, value=dict(default)))

        # optional bootstrap admin for the website (ADMIN_USERNAME / ADMIN_PASSWORD)
        _bootstrap_admin(session)

        # demo coupon (public marketing seed, harmless)
        if not session.execute(
            select(CouponCode).where(CouponCode.code == "WELCOME5")
        ).scalar_one_or_none():
            session.add(
                CouponCode(
                    code="WELCOME5",
                    coupon_type="PERCENT",
                    value=5,
                    min_order_amount=0,
                    per_user_limit=1,
                    max_uses=None,
                    expires_at=None,
                )
            )
    log.info("Seed data ready (games catalog structure, roles, supplier, settings)")


def _bootstrap_admin(session) -> None:
    import os

    from app.models import User
    from app.security import hash_password, validate_password

    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return
    if validate_password(password):
        log.warning("ADMIN_PASSWORD is too weak — bootstrap admin not created")
        return
    role = session.execute(select(Role).where(Role.name == RoleName.ADMIN)).scalar_one_or_none()
    if role is None:
        session.flush()
        role = session.execute(select(Role).where(Role.name == RoleName.ADMIN)).scalar_one()
    user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        session.add(User(username=username, password_hash=hash_password(password), role_id=role.id))
        log.info("bootstrap admin created: %s", username)
    elif user.role_id != role.id:
        user.role_id = role.id

"""Admin-editable site settings (branding, content, top-up rules, FX rates).

Stored in ``admin_settings`` (JSON). NEVER contains secrets — credentials live
in the server-side .env and are managed by ``app.services.env_store``.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminSetting

DEFAULT_BRANDING: dict[str, Any] = {
    "site_name": "VYRON",
    "logo_url": "/assets/img/logo.svg",
    "favicon_url": "/assets/img/logo.svg",
    "hero_image_url": "",
    "og_image_url": "",
    "hero_title_uz": "O'yin valyutasi bir zumda",
    "hero_title_en": "Game currency in seconds",
    "hero_title_ru": "Игровая валюта за секунды",
    "hero_subtitle_uz": "UC, Diamonds, Robux, Gems va boshqalar — balansni karta orqali to'ldiring, avtomatik yetkazib berish.",
    "hero_subtitle_en": "UC, Diamonds, Robux, Gems and more — top up by card, automated delivery.",
    "hero_subtitle_ru": "UC, Diamonds, Robux, Gems и другое — пополнение картой, автоматическая выдача.",
    "announcement_uz": "",
    "announcement_en": "",
    "announcement_ru": "",
    "support_telegram": "",
    "support_phone": "",
    "support_email": "",
    "support_hours": "24/7",
    "instagram_url": "",
    "telegram_channel_url": "",
    "footer_text_uz": "",
    "footer_text_en": "",
    "footer_text_ru": "",
}

# FX rates used to convert supplier prices into UZS during catalog sync.
DEFAULT_RATES: dict[str, Any] = {"UZS": 1}

EDITABLE_KEYS = {"general", "pricing", "branding", "topup", "rates"}

_URL_RE = re.compile(r"^(https?://|/)[^\s\"'<>]*$")


def get_setting(session: Session, key: str, defaults: dict | None = None) -> dict:
    row = session.execute(select(AdminSetting).where(AdminSetting.key == key)).scalar_one_or_none()
    data = dict(defaults or {})
    if row and isinstance(row.value, dict):
        data.update(row.value)
    return data


def put_setting(session: Session, key: str, value: dict) -> dict:
    row = session.execute(select(AdminSetting).where(AdminSetting.key == key)).scalar_one_or_none()
    if row is None:
        row = AdminSetting(key=key, value=value)
        session.add(row)
    else:
        merged = dict(row.value or {})
        merged.update(value)
        row.value = merged
    session.flush()
    return dict(row.value or {})


def get_branding(session: Session) -> dict:
    return get_setting(session, "branding", DEFAULT_BRANDING)


def get_rates(session: Session) -> dict:
    return get_setting(session, "rates", DEFAULT_RATES)


def sanitize_branding(value: dict) -> dict:
    """Allow only known keys; URLs must be http(s) or site-relative; trim text."""
    clean: dict[str, Any] = {}
    for key, default in DEFAULT_BRANDING.items():
        if key not in value:
            continue
        raw = value[key]
        text = "" if raw is None else str(raw).strip()
        if key.endswith("_url"):
            if text and not _URL_RE.match(text):
                raise ValueError(f"invalid_url:{key}")
            clean[key] = text[:512]
        else:
            clean[key] = text[:600]
    return clean


def sanitize_rates(value: dict) -> dict:
    clean: dict[str, Any] = {}
    for code, rate in value.items():
        code = str(code).upper().strip()
        if not re.fullmatch(r"[A-Z]{3,5}", code):
            raise ValueError("invalid_currency")
        try:
            number = float(rate)
        except (TypeError, ValueError):
            raise ValueError("invalid_rate")
        if number <= 0 or number > 10_000_000:
            raise ValueError("invalid_rate")
        clean[code] = number
    clean["UZS"] = 1
    return clean


def sanitize_topup(value: dict) -> dict:
    clean: dict[str, Any] = {}
    if "enabled" in value:
        clean["enabled"] = bool(value["enabled"])
    if "auto_refund_failed_orders" in value:
        clean["auto_refund_failed_orders"] = bool(value["auto_refund_failed_orders"])
    for key in ("min_amount", "max_amount"):
        if key in value:
            number = int(value[key])
            if number < 100 or number > 1_000_000_000:
                raise ValueError(f"invalid:{key}")
            clean[key] = number
    if "presets" in value:
        presets = [int(x) for x in (value["presets"] or []) if int(x) > 0][:8]
        clean["presets"] = presets
    if clean.get("min_amount") and clean.get("max_amount") and clean["min_amount"] > clean["max_amount"]:
        raise ValueError("invalid:min_amount")
    return clean

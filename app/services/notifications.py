"""Customer notifications: in-app + Telegram (when configured)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.i18n import t
from app.logging_config import get_logger
from app.models import Notification, User

log = get_logger("vyron.notifications")


def format_money(minor: Any) -> str:
    """Minor units (tiyin) -> '12 500' so'm string."""
    try:
        return f"{int(minor) // 100:,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(minor)


def push_notification(
    session: Session,
    user: User,
    *,
    kind: str,
    title_key: str,
    body_key: str,
    params: Optional[dict[str, Any]] = None,
) -> Notification:
    note = Notification(
        user_id=user.id,
        kind=kind,
        title_key=title_key,
        body_key=body_key,
        params=params,
    )
    session.add(note)
    return note


async def send_telegram(user: User, text: str) -> None:
    """Best-effort Telegram delivery. Requires TELEGRAM_BOT_TOKEN + user link."""
    settings = get_settings()
    if not settings.telegram_configured() or not user.telegram_id:
        return
    try:
        import httpx

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                url,
                json={
                    "chat_id": user.telegram_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
    except Exception as exc:  # never break order flow on notification errors
        log.warning("Telegram notify failed: %s", type(exc).__name__)


def notify_user(
    session: Session,
    user: User,
    *,
    kind: str,
    title_key: str,
    body_key: str,
    params: Optional[dict[str, Any]] = None,
) -> None:
    push_notification(
        session, user, kind=kind, title_key=title_key, body_key=body_key, params=params
    )
    lang = user.language or "uz"
    params = params or {}
    title = t(title_key, lang, **params)
    body = t(body_key, lang, **params)
    lines = [f"VYRON — {title}", body]
    if "order_number" in params:
        lines.append(f"{t('order_number', lang)}: {params['order_number']}")
    if "status" in params:
        lines.append(f"{t('status', lang)}: {params['status']}")
    if "total" in params:
        lines.append(f"{t('total', lang)}: {format_money(params['total'])} {params.get('currency', '')}")
    if "amount" in params:
        lines.append(f"{t('amount', lang)}: {format_money(params['amount'])} {params.get('currency', '')}")
    _dispatch(send_telegram(user, "\n".join(lines)))


_MAIN_LOOP = None


def set_main_loop(loop) -> None:
    """Remember the server event loop so sync (threadpool) handlers can notify."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def _dispatch(coro) -> None:
    """Fire-and-forget: works from async handlers and from sync threadpool code."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
        return
    except RuntimeError:
        pass
    if _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)
    else:
        coro.close()

"""Shared API dependencies: auth, RBAC, common helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import RoleName, Session as DbSession, User
from app.security import SESSION_COOKIE, hash_token


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    vyron_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: Optional[str] = Header(default=None),
) -> Optional[User]:
    if authorization and authorization.lower().startswith("bearer "):
        vyron_session = authorization[7:].strip() or vyron_session
    if not vyron_session:
        return None
    row = db.execute(
        select(DbSession).where(DbSession.token_hash == hash_token(vyron_session))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at < datetime.utcnow():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    request.state.session_token = vyron_session
    return user


def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def require_admin(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Server-side admin authorization. Telegram admin IDs grant admin role."""
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if user.role and user.role.name == RoleName.ADMIN:
        return user
    # telegram admin IDs are authoritative for telegram-linked accounts
    from app.config import get_settings

    settings = get_settings()
    if user.telegram_id and user.telegram_id in settings.telegram_admin_ids:
        return user
    raise HTTPException(status_code=403, detail="forbidden")


def require_staff(user: User = Depends(get_current_user)) -> User:
    if user is None or not user.is_staff:
        raise HTTPException(status_code=403, detail="forbidden")
    return user


def pagination(page: Optional[int], page_size: Optional[int]) -> tuple[int, int]:
    page = max(1, page or 1)
    page_size = min(max(1, page_size or 20), 100)
    return page, page_size


def verify_webhook_secret(
    x_webhook_secret: Optional[str] = Header(default=None),
) -> None:  # pragma: no cover - optional helper
    return None

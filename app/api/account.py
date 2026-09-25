"""Account API: profile, preferences, notifications."""
from __future__ import annotations

from typing import Optional

from fastapi import Query, APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.db import get_db
from app.i18n import normalize_lang
from app.models import Notification, User

router = APIRouter(prefix="/api/account", tags=["account"])


class ProfileBody(BaseModel):
    email: Optional[EmailStr] = None
    language: Optional[str] = None
    theme: Optional[str] = None


@router.get("/profile")
def profile(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return {
        "profile": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "language": user.language,
            "theme": user.theme,
            "telegram_linked": bool(user.telegram_id),
            "telegram_id": user.telegram_id,
            "balance": int(user.balance or 0),
            "has_password": bool(user.password_hash),
            "role": user.role.name.value if user.role else "CUSTOMER",
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
    }


@router.patch("/profile")
def update_profile(
    body: ProfileBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if body.email is not None:
        existing = db.execute(
            select(User).where(User.email == body.email.lower(), User.id != user.id)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="email_taken")
        user.email = body.email.lower()
    if body.language is not None:
        user.language = normalize_lang(body.language)
    if body.theme is not None:
        if body.theme not in ("day", "night"):
            raise HTTPException(status_code=400, detail="invalid_theme")
        user.theme = body.theme
    db.commit()
    return {"ok": True}


@router.get("/notifications")
def notifications(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    lang: Optional[str] = Query(default=None),
):
    from app.i18n import normalize_lang, t

    lang = normalize_lang(lang or user.language)
    rows = (
        db.execute(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.id.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "title_key": n.title_key,
                "body_key": n.body_key,
                "params": n.params,
                "title": t(n.title_key, lang, **(n.params or {})),
                "body": t(n.body_key, lang, **(n.params or {})),
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ]
    }


@router.post("/notifications/read")
def mark_read(db: Session = Depends(get_db), user: User = Depends(require_user)):
    for n in (
        db.execute(select(Notification).where(Notification.user_id == user.id))
        .scalars()
        .all()
    ):
        n.is_read = True
    db.commit()
    return {"ok": True}


class PasswordBody(BaseModel):
    current_password: Optional[str] = None
    new_password: str


@router.post("/password")
def change_password(
    body: PasswordBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Set (Telegram-only accounts) or change the website login password."""
    from app.security import hash_password, rate_limit, validate_password, verify_password

    if not rate_limit(f"pwchange:{user.id}", 5, 300):
        raise HTTPException(status_code=429, detail="rate_limited")
    if user.password_hash:
        if not body.current_password or not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="invalid_current_password")
    err = validate_password(body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}

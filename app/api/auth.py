"""Authentication API: register / login / logout / Telegram WebApp auth."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.i18n import normalize_lang
from app.models import Role, RoleName, Session as DbSession, TelegramUser, User
from app.api.deps import get_client_ip, get_current_user, require_user
from app.security import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    LOCKOUT_MINUTES,
    MAX_FAILED_LOGINS,
    hash_password,
    hash_token,
    new_reset_token,
    new_session_token,
    rate_limit,
    session_expiry,
    validate_password,
    verify_password,
    verify_telegram_init_data,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,40}$")


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    language: Optional[str] = "uz"


class LoginBody(BaseModel):
    username: str
    password: str


class TelegramBody(BaseModel):
    init_data: str


class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(BaseModel):
    token: str
    password: str = Field(min_length=1, max_length=128)


def _public_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role.name.value if user.role else "CUSTOMER",
        "language": user.language,
        "theme": user.theme,
        "telegram_linked": bool(user.telegram_id),
        "telegram_id": user.telegram_id,
        "balance": int(user.balance or 0),
        "has_password": bool(user.password_hash),
        "is_admin": user.is_admin
        or (
            user.telegram_id is not None
            and user.telegram_id in get_settings().telegram_admin_ids
        ),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _is_https(request: Optional[Request]) -> bool:
    if request is None:
        return get_settings().public_base_url.startswith("https://")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0]
    return proto.strip().lower() == "https"


def _set_session_cookie(response: Response, token: str, request: Optional[Request] = None) -> None:
    secure = _is_https(request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _create_session(db: Session, user: User, request: Request, response: Response) -> str:
    token = new_session_token()
    db.add(
        DbSession(
            user_id=user.id,
            token_hash=hash_token(token),
            ip=get_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255],
            expires_at=session_expiry(),
        )
    )
    _set_session_cookie(response, token, request)
    return token


@router.post("/register")
def register(
    body: RegisterBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    if not rate_limit(f"register:{ip}", 5, 300):
        raise HTTPException(status_code=429, detail="rate_limited")
    if not USERNAME_RE.match(body.username):
        raise HTTPException(status_code=400, detail="invalid_username")
    pwd_error = validate_password(body.password)
    if pwd_error:
        raise HTTPException(status_code=400, detail=pwd_error)

    exists = db.execute(
        select(User).where(
            (func.lower(User.username) == body.username.lower())
            | (func.lower(User.email) == body.email.lower())
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="user_exists")

    role = db.execute(select(Role).where(Role.name == RoleName.CUSTOMER)).scalar_one()
    user = User(
        username=body.username,
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role_id=role.id,
        language=normalize_lang(body.language),
    )
    db.add(user)
    db.flush()
    _create_session(db, user, request, response)
    db.commit()
    return {"user": _public_user(user)}


@router.post("/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    if not rate_limit(f"login:{ip}", 10, 300):
        raise HTTPException(status_code=429, detail="rate_limited")

    user = db.execute(
        select(User).where(func.lower(User.username) == body.username.lower())
    ).scalar_one_or_none()
    if user is None and "@" in body.username:
        user = db.execute(
            select(User).where(func.lower(User.email) == body.username.lower())
        ).scalar_one_or_none()

    now = datetime.utcnow()
    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise HTTPException(status_code=429, detail="account_locked")

    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_count = 0
            db.commit()
        raise HTTPException(status_code=401, detail="invalid_credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="account_disabled")

    user.failed_login_count = 0
    user.locked_until = None
    _create_session(db, user, request, response)
    db.commit()
    return {"user": _public_user(user)}


@router.post("/telegram")
def telegram_auth(
    body: TelegramBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Login/register via verified Telegram WebApp initData (HMAC checked)."""
    ip = get_client_ip(request)
    if not rate_limit(f"tgauth:{ip}", 20, 60):
        raise HTTPException(status_code=429, detail="rate_limited")

    settings = get_settings()
    if not settings.telegram_configured():
        raise HTTPException(status_code=503, detail="telegram_not_configured")

    tg_user = verify_telegram_init_data(body.init_data, settings.telegram_bot_token)
    if tg_user is None or not tg_user.get("id"):
        raise HTTPException(status_code=401, detail="invalid_telegram_data")

    telegram_id = int(tg_user["id"])
    user = db.execute(
        select(User).where(User.telegram_id == telegram_id)
    ).scalar_one_or_none()

    tg_row = db.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
    ).scalar_one_or_none()
    if tg_row is None:
        tg_row = TelegramUser(telegram_id=telegram_id)
        db.add(tg_row)
    tg_row.username = tg_user.get("username")
    tg_row.first_name = tg_user.get("first_name")
    tg_row.language = normalize_lang(tg_user.get("language_code"))
    tg_row.updated_at = datetime.utcnow()

    is_tg_admin = telegram_id in settings.telegram_admin_ids

    if user is None:
        username = tg_user.get("username") or f"tg_{telegram_id}"
        base = re.sub(r"[^a-zA-Z0-9_.-]", "", username)[:30] or f"tg{telegram_id}"
        candidate = base
        i = 1
        while db.execute(select(User).where(User.username == candidate)).scalar_one_or_none():
            candidate = f"{base}{i}"
            i += 1
        role_name = RoleName.ADMIN if is_tg_admin else RoleName.CUSTOMER
        role = db.execute(select(Role).where(Role.name == role_name)).scalar_one()
        user = User(
            username=candidate,
            telegram_id=telegram_id,
            role_id=role.id,
            language=tg_row.language,
        )
        db.add(user)
        db.flush()
    else:
        user.telegram_id = telegram_id
        if is_tg_admin and not user.is_admin:
            role = db.execute(select(Role).where(Role.name == RoleName.ADMIN)).scalar_one()
            user.role_id = role.id
        elif not is_tg_admin and user.is_admin and not user.password_hash:
            # Telegram-only account removed from TELEGRAM_ADMIN_IDS -> demote.
            role = db.execute(select(Role).where(Role.name == RoleName.CUSTOMER)).scalar_one()
            user.role_id = role.id
    tg_row.user_id = user.id

    token = _create_session(db, user, request, response)
    db.commit()
    db.refresh(user)
    # Telegram Web App may run in a third-party iframe (web.telegram.org) where
    # cookies are blocked: the client keeps this token in sessionStorage and
    # sends it as "Authorization: Bearer ...".
    return {"user": _public_user(user), "token": token}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user),
):
    token = getattr(request.state, "session_token", None)
    if user is not None and token:
        row = db.execute(
            select(DbSession).where(DbSession.token_hash == hash_token(token))
        ).scalar_one_or_none()
        if row is not None:
            row.revoked_at = datetime.utcnow()
    response.delete_cookie(SESSION_COOKIE, path="/")
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: Optional[User] = Depends(get_current_user)):
    if user is None:
        return {"user": None}
    return {"user": _public_user(user)}


@router.post("/password/forgot")
def password_forgot(body: ForgotBody, request: Request, db: Session = Depends(get_db)):
    """Password reset architecture: create token; delivery is admin/ops action.

    The reset token is NOT returned by the API (no account enumeration).
    Configure an email provider to deliver it automatically.
    """
    ip = get_client_ip(request)
    if not rate_limit(f"forgot:{ip}", 5, 300):
        raise HTTPException(status_code=429, detail="rate_limited")
    user = db.execute(
        select(User).where(func.lower(User.email) == body.email.lower())
    ).scalar_one_or_none()
    if user is not None:
        user.password_reset_token = new_reset_token()
        user.password_reset_expires = datetime.utcnow() + timedelta(hours=2)
        db.commit()
        # Delivery: via the Telegram bot when the account is linked to Telegram.
        if user.telegram_id and get_settings().telegram_configured():
            from app.i18n import t as _t
            from app.services.notifications import _dispatch, send_telegram

            base = get_settings().base_url() or str(request.base_url).rstrip("/")
            link = f"{base}/#/reset?token={user.password_reset_token}"
            lang = user.language or "uz"
            _dispatch(send_telegram(user, f"VYRON — {_t('password_reset', lang)}\n{link}"))
    # Always the same response (no enumeration).
    return {"ok": True, "message": "reset_requested"}


@router.post("/password/reset")
def password_reset(body: ResetBody, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    if not rate_limit(f"reset:{ip}", 5, 300):
        raise HTTPException(status_code=429, detail="rate_limited")
    pwd_error = validate_password(body.password)
    if pwd_error:
        raise HTTPException(status_code=400, detail=pwd_error)
    user = db.execute(
        select(User).where(User.password_reset_token == body.token)
    ).scalar_one_or_none()
    if (
        user is None
        or user.password_reset_expires is None
        or user.password_reset_expires < datetime.utcnow()
    ):
        raise HTTPException(status_code=400, detail="invalid_token")
    user.password_hash = hash_password(body.password)
    user.password_reset_token = None
    user.password_reset_expires = None
    db.commit()
    return {"ok": True}

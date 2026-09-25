"""Security primitives: password hashing, sessions, Telegram verification,
rate limiting, secure headers. Constant-time comparisons everywhere.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

import bcrypt

from app.config import get_settings

SESSION_COOKIE = "vyron_session"
SESSION_TTL_DAYS = 14
MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15

_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


# ---- passwords --------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ---- tokens / sessions ------------------------------------------------------
def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_idempotency_key() -> str:
    return secrets.token_urlsafe(24)


def new_reset_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return datetime.utcnow() + timedelta(days=SESSION_TTL_DAYS)


# ---- Telegram WebApp initData verification ----------------------------------
def verify_telegram_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """Validate Telegram WebApp initData per official algorithm.

    HMAC-SHA256 over the data-check-string keyed with SHA256(bot_token).
    Returns the parsed user dict on success, None on failure.
    """
    if not init_data or not bot_token:
        return None
    try:
        parsed: dict[str, str] = {}
        for pair in init_data.split("&"):
            key, _, value = pair.partition("=")
            parsed[key] = value
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
        calculated = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None
        # auth_date freshness (max 24h)
        try:
            auth_date = int(parsed.get("auth_date", "0"))
        except ValueError:
            return None
        if auth_date and time.time() - auth_date > 24 * 3600:
            return None
        if "user" in parsed:
            return json.loads(parsed["user"])
        return {}
    except Exception:
        return None


def verify_hmac_signature(
    secret: str, payload: bytes, timestamp: str, signature: str
) -> bool:
    """HMAC-SHA256 over `timestamp + '.' + rawBody` with constant-time compare."""
    if not secret or not signature:
        return False
    signed = timestamp.encode("utf-8") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_hmac_body_sha256(secret: str, payload: bytes, signature: str) -> bool:
    """Plain HMAC-SHA256 of raw body (hex) with constant-time compare."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---- rate limiting (in-process; swap for Redis in multi-node deployments) ---
def rate_limit(key: str, limit: int, window_seconds: int = 60) -> bool:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return True
    now = time.time()
    bucket = _rate_buckets[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


# ---- password policy --------------------------------------------------------
def validate_password(password: str) -> Optional[str]:
    if not password or len(password) < 8:
        return "password_too_short"
    if len(password) > 128:
        return "password_too_long"
    if password.isalpha() or password.isdigit():
        return "password_too_simple"
    return None

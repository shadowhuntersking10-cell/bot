"""VYRON application configuration.

Secrets are read from the environment (optionally via a local .env file).
Nothing secret is ever written to logs, API responses or the database.

Admins can update integration credentials from the admin panel: those
values are written ONLY to the server-side .env file (write-only; never
returned by any API) and applied at runtime through ``Settings.reload()``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

BASE_DIR = Path(__file__).resolve().parent.parent


def env_file_path() -> Path:
    custom = os.environ.get("VYRON_ENV_FILE")
    return Path(custom) if custom else BASE_DIR / ".env"


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or env_file_path()
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _load_dotenv(override: bool = False) -> None:
    """Minimal .env loader (no extra dependency)."""
    for key, value in read_env_file().items():
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


_load_dotenv()


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        env = os.environ
        self.app_env: str = env.get("APP_ENV", "production")
        self.app_debug: bool = _bool("APP_DEBUG", "false")
        self.public_base_url: str = env.get("PUBLIC_BASE_URL", "").rstrip("/")
        self.webapp_url: str = (env.get("WEBAPP_URL") or self.public_base_url).rstrip("/")
        self.session_secret: str = env.get("SESSION_SECRET", "")
        self.database_url: str = self._build_database_url()
        self.database_fallback_sqlite: bool = _bool("DATABASE_FALLBACK_SQLITE", "false")

        # Supplier (Payerpin)
        self.payerpin_api_key: str = env.get("PAYERPIN_API_KEY", "")
        self.payerpin_base_url: str = env.get(
            "PAYERPIN_BASE_URL", "https://api.payerpin.uz"
        ).rstrip("/")
        self.payerpin_webhook_secret: str = env.get("PAYERPIN_WEBHOOK_SECRET", "")

        # Hamyon API (automatic HUMO/UZCARD card payments — wallet top-up)
        self.hamyon_shop_id: str = env.get("HAMYON_SHOP_ID", "").strip()
        self.hamyon_shop_key: str = env.get("HAMYON_SHOP_KEY", "").strip()
        self.hamyon_base_url: str = env.get("HAMYON_BASE_URL", "https://hamyon-api.uz").rstrip("/")

        # Optional generic signed-webhook payment provider (direct order payment)
        self.payment_provider: str = env.get("PAYMENT_PROVIDER", "").strip().lower()
        self.payment_api_key: str = env.get("PAYMENT_API_KEY", "")
        self.payment_secret: str = env.get("PAYMENT_SECRET", "")
        self.payment_webhook_secret: str = env.get("PAYMENT_WEBHOOK_SECRET", "")
        self.payment_currency: str = env.get("PAYMENT_CURRENCY", "UZS").upper()

        # Telegram
        self.telegram_bot_token: str = env.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_admin_ids: list[int] = self._parse_admin_ids(
            env.get("TELEGRAM_ADMIN_IDS", "")
        )

        self.host: str = env.get("HOST", "0.0.0.0")
        self.port: int = int(env.get("PORT", "8000") or 8000)
        self.log_level: str = env.get("LOG_LEVEL", "INFO").upper()
        self.fulfillment_worker_enabled: bool = _bool("FULFILLMENT_WORKER_ENABLED", "true")
        self.fulfillment_poll_seconds: int = int(env.get("FULFILLMENT_POLL_SECONDS", "5") or 5)
        self.catalog_sync_interval_minutes: int = int(
            env.get("CATALOG_SYNC_INTERVAL_MINUTES", "60") or 60
        )
        self.rate_limit_enabled: bool = _bool("RATE_LIMIT_ENABLED", "true")
        self.upload_dir: Path = Path(env.get("UPLOAD_DIR", str(BASE_DIR / "uploads")))

    def reload(self) -> None:
        """Re-read .env into the process environment and refresh in place.

        The object identity is kept so every module holding a reference sees
        the new values immediately.
        """
        _load_dotenv(override=True)
        self._load()

    # ------------------------------------------------------------------
    @staticmethod
    def _build_database_url() -> str:
        """DATABASE_URL wins; otherwise build a MySQL URL from DB_* parts.

        A dedicated (non-root) MySQL user is expected — see deploy/mysql_setup.sql.
        """
        env = os.environ
        url = env.get("DATABASE_URL", "").strip()
        if url:
            return url
        user = env.get("DB_USER", "vyron")
        password = env.get("DB_PASSWORD", "")
        host = env.get("DB_HOST", "127.0.0.1")
        port = env.get("DB_PORT", "3306")
        name = env.get("DB_NAME", "vyron")
        auth = quote_plus(user) + (f":{quote_plus(password)}" if password else "")
        return f"mysql+pymysql://{auth}@{host}:{port}/{name}?charset=utf8mb4"

    @staticmethod
    def _parse_admin_ids(raw: str) -> list[int]:
        ids: list[int] = []
        for part in raw.replace(";", ",").replace(" ", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return sorted(set(ids))

    # ---- secret-safe helpers -------------------------------------------------
    def payerpin_configured(self) -> bool:
        return bool(self.payerpin_api_key)

    def hamyon_configured(self) -> bool:
        return bool(self.hamyon_shop_id and self.hamyon_shop_key)

    def payment_configured(self) -> bool:
        """Any way to pay exists: Hamyon wallet top-ups or a signed-webhook provider."""
        return self.hamyon_configured() or self.provider_payment_configured()

    def provider_payment_configured(self) -> bool:
        return bool(self.payment_provider and self.payment_webhook_secret)

    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token)

    def base_url(self) -> str:
        return self.public_base_url or ""

    def require_session_secret(self) -> str:
        if self.session_secret and len(self.session_secret) >= 16:
            return self.session_secret
        if self.app_env == "production":
            raise RuntimeError(
                "SESSION_SECRET is not configured. Set a long random SESSION_SECRET "
                "in the environment (.env) before starting VYRON in production."
            )
        return "dev-only-insecure-session-secret-change-me"

    def safe_summary(self) -> dict:
        """Configuration state WITHOUT any secret values."""
        return {
            "app_env": self.app_env,
            "database": self.database_url.split("://", 1)[0] if self.database_url else None,
            "payerpin_configured": self.payerpin_configured(),
            "payerpin_base_url": self.payerpin_base_url,
            "hamyon_configured": self.hamyon_configured(),
            "payment_provider": self.payment_provider or None,
            "payment_configured": self.payment_configured(),
            "telegram_configured": self.telegram_configured(),
            "telegram_admins_configured": bool(self.telegram_admin_ids),
            "rate_limit_enabled": self.rate_limit_enabled,
            "fulfillment_worker_enabled": self.fulfillment_worker_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

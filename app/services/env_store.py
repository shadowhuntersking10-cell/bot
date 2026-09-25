"""Write-only management of integration credentials from the admin panel.

* Values are written ONLY to the server-side .env file (chmod 600), never to
  the database, logs or API responses.
* Secret keys are reported as ``configured: true/false`` only.
* Non-secret keys (URLs, shop id, admin IDs) are shown so admins can verify them.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from app.config import env_file_path, get_settings, read_env_file

# key -> (group, secret?, restart_required?)
MANAGED_KEYS: dict[str, tuple[str, bool, bool]] = {
    "PUBLIC_BASE_URL": ("general", False, False),
    "WEBAPP_URL": ("general", False, False),
    "TELEGRAM_BOT_TOKEN": ("telegram", True, False),
    "TELEGRAM_ADMIN_IDS": ("telegram", False, False),
    "HAMYON_SHOP_ID": ("hamyon", False, False),
    "HAMYON_SHOP_KEY": ("hamyon", True, False),
    "HAMYON_BASE_URL": ("hamyon", False, False),
    "PAYERPIN_API_KEY": ("payerpin", True, False),
    "PAYERPIN_BASE_URL": ("payerpin", False, False),
    "PAYERPIN_WEBHOOK_SECRET": ("payerpin", True, False),
    "PAYMENT_PROVIDER": ("payment", False, False),
    "PAYMENT_API_KEY": ("payment", True, False),
    "PAYMENT_SECRET": ("payment", True, False),
    "PAYMENT_WEBHOOK_SECRET": ("payment", True, False),
    "CATALOG_SYNC_INTERVAL_MINUTES": ("general", False, True),
}

_VALIDATORS = {
    "PUBLIC_BASE_URL": r"^(https?://[^\s]+)?$",
    "WEBAPP_URL": r"^(https?://[^\s]+)?$",
    "HAMYON_BASE_URL": r"^(https://[^\s]+)?$",
    "PAYERPIN_BASE_URL": r"^(https://[^\s]+)?$",
    "TELEGRAM_BOT_TOKEN": r"^(\d{5,15}:[A-Za-z0-9_-]{20,})?$",
    "TELEGRAM_ADMIN_IDS": r"^[\d,\s]*$",
    "PAYMENT_PROVIDER": r"^(|generic|sandbox)$",
    "CATALOG_SYNC_INTERVAL_MINUTES": r"^\d{0,5}$",
}


class EnvStoreError(ValueError):
    pass


def describe() -> list[dict]:
    """Current state of managed keys WITHOUT secret values."""
    file_values = read_env_file()
    items = []
    for key, (group, secret, restart) in MANAGED_KEYS.items():
        value = os.environ.get(key, file_values.get(key, ""))
        entry = {
            "key": key,
            "group": group,
            "secret": secret,
            "restart_required": restart,
            "configured": bool(value),
        }
        if not secret:
            entry["value"] = value
        items.append(entry)
    return items


def _validate(key: str, value: str) -> str:
    if key not in MANAGED_KEYS:
        raise EnvStoreError(f"unknown_key:{key}")
    value = (value or "").strip()
    if any(ch in value for ch in "\r\n\0") or len(value) > 512:
        raise EnvStoreError(f"invalid_value:{key}")
    pattern = _VALIDATORS.get(key)
    if pattern and not re.fullmatch(pattern, value):
        raise EnvStoreError(f"invalid_value:{key}")
    if key.endswith("_URL"):
        value = value.rstrip("/")
    if key == "TELEGRAM_ADMIN_IDS":
        value = ",".join(p for p in re.split(r"[,\s]+", value) if p)
    return value


def update(values: dict[str, str]) -> list[str]:
    """Validate + persist. Returns list of changed keys (names only)."""
    clean = {k: _validate(k, v) for k, v in values.items()}
    path: Path = env_file_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(clean)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={_quote(remaining.pop(key))}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={_quote(value)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out).rstrip("\n") + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    for key, value in clean.items():
        os.environ[key] = value
    get_settings().reload()
    return sorted(clean.keys())


def _quote(value: str) -> str:
    if value == "" or re.fullmatch(r"[A-Za-z0-9_:/.,@+-]*", value):
        return value
    return '"' + value.replace('"', "") + '"'

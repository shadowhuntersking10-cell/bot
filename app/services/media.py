"""Admin media uploads (logo, banners, game/product images).

Images are verified by decoding them with Pillow and re-encoded (metadata
stripped). SVG is intentionally NOT accepted (script injection risk).
"""
from __future__ import annotations

import io
import secrets
from datetime import datetime
from pathlib import Path

from app.config import get_settings

MAX_BYTES = 6 * 1024 * 1024
MAX_SIDE = 2400
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "ICO"}


class MediaError(ValueError):
    pass


def upload_dir() -> Path:
    path = get_settings().upload_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(data: bytes, *, kind: str = "media") -> dict:
    if not data:
        raise MediaError("empty_file")
    if len(data) > MAX_BYTES:
        raise MediaError("file_too_large")
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise MediaError("pillow_missing") from exc
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        image = Image.open(io.BytesIO(data))
        fmt = (image.format or "").upper()
    except Exception as exc:
        raise MediaError("invalid_image") from exc
    if fmt not in ALLOWED_FORMATS:
        raise MediaError("unsupported_format")

    safe_kind = "".join(ch for ch in kind if ch.isalnum() or ch in "-_")[:24] or "media"
    stamp = datetime.utcnow().strftime("%Y%m%d")
    token = secrets.token_hex(6)

    if fmt in ("GIF", "ICO"):
        ext = fmt.lower()
        name = f"{safe_kind}-{stamp}-{token}.{ext}"
        (upload_dir() / name).write_bytes(data)
        width, height = image.size
    else:
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        width, height = image.size
        name = f"{safe_kind}-{stamp}-{token}.webp"
        buf = io.BytesIO()
        image.save(buf, "WEBP", quality=88, method=4)
        (upload_dir() / name).write_bytes(buf.getvalue())
    return {"url": f"/uploads/{name}", "name": name, "width": width, "height": height}


def list_media(limit: int = 200) -> list[dict]:
    folder = upload_dir()
    files = sorted(
        (p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    return [
        {"url": f"/uploads/{p.name}", "name": p.name, "size": p.stat().st_size,
         "created_at": datetime.utcfromtimestamp(p.stat().st_mtime).isoformat()}
        for p in files
    ]


def delete_media(name: str) -> bool:
    if "/" in name or "\\" in name or name.startswith("."):
        raise MediaError("invalid_name")
    path = upload_dir() / name
    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False

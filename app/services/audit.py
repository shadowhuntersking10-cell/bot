"""Immutable audit logging for important admin actions (never logs secrets)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import AuditLog

log = get_logger("vyron.audit")


def write_audit(
    session: Session,
    *,
    admin_user_id: Optional[int],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    result: str = "SUCCESS",
    details: Optional[dict[str, Any]] = None,
) -> None:
    safe_details = None
    if details:
        safe_details = {
            k: ("[REDACTED]" if any(s in k.lower() for s in ("key", "secret", "token", "password")) else v)
            for k, v in details.items()
        }
    session.add(
        AuditLog(
            admin_user_id=admin_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            result=result,
            details=safe_details,
        )
    )
    log.info(
        "audit action=%s target=%s:%s result=%s admin=%s",
        action,
        target_type,
        target_id,
        result,
        admin_user_id,
    )

"""Structured application logging. Secrets are never logged."""
from __future__ import annotations

import logging
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|authorization)\s*[=:]\s*\S+"),
    re.compile(r"pp_live_\w+"),
    re.compile(r"\d+:[A-Za-z0-9_-]{35}"),  # telegram bot tokens
]

_REDACTED = "[REDACTED]"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            for pattern in _SECRET_PATTERNS:
                message = pattern.sub(_REDACTED, message)
            record.msg = message
            record.args = ()
        except Exception:  # pragma: no cover - logging must never explode
            pass
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(RedactingFilter())
    root.handlers = [handler]
    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

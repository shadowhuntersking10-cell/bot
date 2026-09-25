"""SQLAlchemy engine / session management (MySQL-first, SQLite fallback)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("vyron.db")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None
db_dialect: str = ""


def _make_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_recycle"] = 1800
        if url.startswith("mysql") and "charset=" not in url:
            kwargs["connect_args"] = {"charset": "utf8mb4"}
    return create_engine(url, **kwargs)


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, connection_record):  # pragma: no cover
    try:
        module = type(dbapi_connection).__module__ or ""
        if "sqlite" in module:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    except Exception:
        pass


def init_engine() -> Engine:
    """Create the engine. MySQL is primary; SQLite fallback keeps dev/tests alive."""
    global _engine, _SessionFactory, db_dialect
    settings = get_settings()
    url = settings.database_url
    try:
        engine = _make_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _engine = engine
        db_dialect = engine.dialect.name
        log.info("Database connected: dialect=%s", db_dialect)
    except Exception as exc:
        if not settings.database_fallback_sqlite:
            safe_url = url.split("@", 1)[-1] if "@" in url else url.split("://", 1)[0]
            log.error(
                "Database connection failed (%s: %s) — target=%s. Check DB_HOST/DB_PORT/"
                "DB_NAME/DB_USER/DB_PASSWORD (or DATABASE_URL) in .env and make sure the "
                "MySQL user exists (see deploy/mysql_setup.sql).",
                type(exc).__name__, str(exc).split("\n")[0][:200], safe_url,
            )
            raise
        fallback = f"sqlite:///{BASE_SQLITE_PATH}"
        log.warning(
            "MySQL connection failed (%s). Falling back to SQLite at %s. "
            "Configure DATABASE_URL for production MySQL.",
            type(exc).__name__,
            fallback,
        )
        engine = _make_engine(fallback)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _engine = engine
        db_dialect = engine.dialect.name
    _SessionFactory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


BASE_SQLITE_PATH = "runtime/vyron.sqlite3"


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    if _SessionFactory is None:
        init_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for scripts, workers and the bot."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request with rollback on error."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

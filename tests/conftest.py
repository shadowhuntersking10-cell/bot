"""Pytest fixtures — isolated SQLite DB, sandbox payment provider, no secrets."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# Environment MUST be set before app.config import (settings are cached).
_TMP = Path(tempfile.mkdtemp(prefix="vyron-test-"))
_TEST_DB = _TMP / "test.sqlite3"
os.environ["VYRON_ENV_FILE"] = str(_TMP / "test.env")  # never touch the real .env
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["HAMYON_SHOP_ID"] = ""
os.environ["HAMYON_SHOP_KEY"] = ""
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ["DATABASE_FALLBACK_SQLITE"] = "true"
os.environ["SESSION_SECRET"] = "test-session-secret-0123456789abcdef"
os.environ["APP_ENV"] = "test"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FULFILLMENT_WORKER_ENABLED"] = "false"
os.environ["PAYMENT_PROVIDER"] = "sandbox"
os.environ["PAYMENT_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["PAYERPIN_API_KEY"] = ""
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_engine  # noqa: E402
from app.migrations import run_migrations, seed_database  # noqa: E402

init_engine()
run_migrations()
seed_database()

from main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def fresh(client):
    """Per-test clean state helper (sessions kept simple: unique usernames)."""
    return client


class UserFactory:
    counter = 0

    @classmethod
    def register(cls, client, *, username=None, email=None, password="Sup3rSecret!"):
        cls.counter += 1
        username = username or f"user{cls.counter}_{os.getpid()}"
        email = email or f"{username}@example.com"
        res = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        assert res.status_code == 200, res.text
        return res.json()["user"]


@pytest.fixture()
def user_factory():
    return UserFactory


@pytest.fixture()
def make_admin(client, user_factory):
    def _make(_client=None):
        user = user_factory.register(client)
        from app.db import session_scope
        from app.models import Role, RoleName, User
        from sqlalchemy import select

        with session_scope() as session:
            db_user = session.execute(
                select(User).where(User.id == user["id"])
            ).scalar_one()
            role = session.execute(
                select(Role).where(Role.name == RoleName.ADMIN)
            ).scalar_one()
            db_user.role_id = role.id
        return user

    return _make


@pytest.fixture()
def seed_variant():
    """A priced variant with a REAL supplier mapping (test data, not fake runtime data)."""
    from app.db import session_scope
    from app.models import Game, Product, ProductType, ProductVariant
    from sqlalchemy import select

    with session_scope() as session:
        game = session.execute(select(Game).where(Game.slug == "pubg-mobile")).scalar_one()
        product = session.execute(
            select(Product).where(Product.game_id == game.id)
        ).scalars().first()
        product.active = True
        product.required_fields = {
            "fields": [
                {"key": "player_id", "label": "Player ID", "type": "alphanum", "required": True},
                {"key": "zone_id", "label": "Zone ID", "type": "number", "required": False},
            ]
        }
        variant = ProductVariant(
            product_id=product.id,
            name="60 UC",
            amount_label="60 UC",
            supplier_product_id="sp_pubg_60",
            supplier_variation_id="var_pubg_60",
            supplier_cost=12000,
            supplier_currency="UZS",
            active=True,
            in_stock=True,
        )
        session.add(variant)
        session.flush()
        from app.services.pricing import compute_price

        breakdown = compute_price(session, variant)
        variant.price_amount = breakdown.unit_price
        return {
            "product_id": product.id,
            "variant_id": variant.id,
            "price": breakdown.unit_price,
            "supplier_cost": breakdown.supplier_cost,
        }

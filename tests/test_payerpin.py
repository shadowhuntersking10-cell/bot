"""PAYERPIN provider tests: endpoints, auth header, mapping, NOT CONFIGURED honesty."""
from __future__ import annotations

import asyncio

import pytest

from app.providers.suppliers.base import SupplierError, SupplierNotConfigured
from app.providers.suppliers.payerpin import (
    PayerpinProvider,
    map_supplier_status,
)


def test_not_configured_without_api_key():
    provider = PayerpinProvider()
    assert provider.configured is False
    with pytest.raises(SupplierNotConfigured):
        asyncio.run(provider.get_catalog())


def test_status_mapping_unknown_never_completes():
    assert map_supplier_status("completed") == "COMPLETED"
    assert map_supplier_status("SUCCESS") == "COMPLETED"
    assert map_supplier_status("processing") == "PROCESSING"
    assert map_supplier_status("failed") == "FAILED"
    # undocumented statuses must NEVER become COMPLETED
    assert map_supplier_status("weird_new_status") == "UNKNOWN"
    assert map_supplier_status(None) == "UNKNOWN"


def test_provider_endpoints_and_auth_header(monkeypatch):
    """Verify the official v2 endpoints + X-API-Key header are used. No invented URLs."""
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"id": 7, "username": "reseller"}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, json=None, params=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setenv("PAYERPIN_API_KEY", "test-key-not-real")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.providers.suppliers.payerpin.httpx.AsyncClient", FakeClient)

    provider = PayerpinProvider()
    assert provider.configured is True

    asyncio.run(provider.verify_connection())
    assert captured["url"] == "https://api.payerpin.uz/api/v2/me"
    assert captured["headers"]["X-API-Key"] == "test-key-not-real"

    asyncio.run(provider.get_balance())
    assert captured["url"].endswith("/api/v2/balance")

    asyncio.run(provider.get_catalog())
    assert captured["url"].endswith("/api/v2/catalog")

    asyncio.run(provider.get_game_catalog("pubg"))
    assert captured["url"].endswith("/api/v2/catalog/pubg")

    asyncio.run(
        provider.create_order(
            supplier_product_id="sp1",
            variation_id="v1",
            player_info={"player_id": "AB12"},
            idempotency_key="idem-1",
            reference="VYR-1",
        )
    )
    assert captured["url"].endswith("/api/v2/order")
    assert captured["method"] == "POST"
    assert captured["json"]["productId"] == "sp1"
    assert captured["json"]["variationId"] == "v1"
    assert captured["json"]["idempotencyKey"] == "idem-1"

    asyncio.run(provider.get_order_status("99"))
    assert captured["url"].endswith("/api/v2/order/99")

    get_settings.cache_clear()


def test_create_order_status_mapping(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        payload = {"data": {"id": "ord-55", "status": "processing"}}

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            return FakeResponse(self.payload)

    monkeypatch.setenv("PAYERPIN_API_KEY", "test-key-not-real")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.providers.suppliers.payerpin.httpx.AsyncClient", FakeClient)

    provider = PayerpinProvider()
    result = asyncio.run(
        provider.create_order(
            supplier_product_id="sp1",
            variation_id=None,
            player_info={},
            idempotency_key="idem-2",
            reference="VYR-2",
        )
    )
    assert result.supplier_order_id == "ord-55"
    assert result.status == "PROCESSING"

    # unknown supplier status must not be COMPLETED
    FakeClient.payload = {"data": {"id": "ord-56", "status": "mystery"}}
    result = asyncio.run(
        provider.create_order(
            supplier_product_id="sp1",
            variation_id=None,
            player_info={},
            idempotency_key="idem-3",
            reference="VYR-3",
        )
    )
    assert result.status == "UNKNOWN"

    get_settings.cache_clear()


def test_temporary_errors_are_retryable(monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setenv("PAYERPIN_API_KEY", "test-key-not-real")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.providers.suppliers.payerpin.httpx.AsyncClient", FakeClient)

    provider = PayerpinProvider()
    with pytest.raises(SupplierError) as exc:
        asyncio.run(provider.get_balance())
    assert exc.value.retryable is True

    get_settings.cache_clear()


def test_never_stores_api_key_in_supplier_config():
    """Supplier config in DB must never contain the API key."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Supplier

    with session_scope() as session:
        supplier = session.execute(select(Supplier).where(Supplier.code == "payerpin")).scalar_one()
        text = str(supplier.config)
        assert "PAYERPIN_API_KEY" not in text
        assert "X-API-Key" not in text

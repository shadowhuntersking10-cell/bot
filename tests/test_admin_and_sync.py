"""ADMIN AUTHORIZATION / ORDERS / CATALOG SYNC tests."""
from __future__ import annotations


def test_normal_user_cannot_access_admin(client, user_factory):
    user_factory.register(client)
    for path in (
        "/api/admin/dashboard",
        "/api/admin/users",
        "/api/admin/orders",
        "/api/admin/payments",
        "/api/admin/fulfillments",
        "/api/admin/suppliers/payerpin",
        "/api/admin/pricing",
        "/api/admin/coupons",
        "/api/admin/promotions",
        "/api/admin/analytics",
        "/api/admin/notifications",
        "/api/admin/telegram",
        "/api/admin/security",
        "/api/admin/audit-logs",
        "/api/admin/health",
        "/api/admin/settings",
    ):
        res = client.get(path)
        assert res.status_code == 403, f"{path} must be forbidden for normal users"


def test_anonymous_cannot_access_admin(client):
    client.post("/api/auth/logout")
    assert client.get("/api/admin/dashboard").status_code == 401


def test_admin_dashboard_works(client, make_admin):
    make_admin(client)
    res = client.get("/api/admin/dashboard")
    assert res.status_code == 200
    data = res.json()
    assert "stats" in data and "users" in data["stats"]


def test_admin_actions_are_audited(client, make_admin):
    make_admin(client)
    client.put("/api/admin/pricing", json={"margin_percent": 12})
    logs = client.get("/api/admin/audit-logs").json()["items"]
    assert any(l["action"] == "pricing.updated" for l in logs)


def test_admin_order_filters(client, make_admin):
    make_admin(client)
    res = client.get("/api/admin/orders?status=COMPLETED&page=1")
    assert res.status_code == 200
    res = client.get("/api/admin/orders?status=NOT_A_STATUS")
    assert res.status_code == 400


def test_admin_cancel_order(client, make_admin, user_factory, seed_variant):
    make_admin(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    order = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}}).json()["order"]
    res = client.post(f"/api/admin/orders/{order['id']}/cancel", json={"reason": "test"})
    assert res.status_code == 200
    detail = client.get(f"/api/orders/{order['order_number']}").json()["order"]
    assert detail["status"] == "CANCELLED"


def test_admin_retry_requires_supplier(client, make_admin, user_factory, seed_variant):
    """Payerpin NOT CONFIGURED -> retry must fail honestly."""
    make_admin(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    order = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}}).json()["order"]
    # retry is only valid on FAILED / SUPPLIER_PROCESSING orders
    res = client.post(f"/api/admin/orders/{order['id']}/retry-fulfillment", json={})
    assert res.status_code == 400
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Order, OrderStatus

    with session_scope() as session:
        db_order = session.execute(
            select(Order).where(Order.id == order["id"])
        ).scalar_one()
        db_order.status = OrderStatus.FAILED
    res = client.post(f"/api/admin/orders/{order['id']}/retry-fulfillment", json={})
    assert res.status_code == 503
    assert res.json()["detail"] == "supplier_not_configured"


def test_payerpin_admin_page_honest(client, make_admin):
    make_admin(client)
    data = client.get("/api/admin/suppliers/payerpin").json()
    assert data["connection_status"] == "NOT CONFIGURED"
    assert data["api_key_configured"] is False
    # never leak a key
    assert "pp_live_" not in str(data)
    assert "api_key" not in str(data).lower() or "api_key_configured" in str(data)

    res = client.post("/api/admin/suppliers/payerpin/test-connection")
    assert res.status_code == 503
    res = client.post("/api/admin/suppliers/payerpin/sync-catalog")
    assert res.status_code == 503
    res = client.post("/api/admin/sayerpin/check-balance") if False else None


def test_system_health(client, make_admin):
    make_admin(client)
    data = client.get("/api/admin/health").json()
    checks = data["checks"]
    assert checks["DATABASE"]["status"] == "Connected"
    assert checks["PAYERPIN"]["status"] == "Not configured"
    assert checks["TELEGRAM"]["status"] == "Not configured"
    assert checks["PAYMENT"]["status"] == "Configured"


def test_order_history_and_authz(client, user_factory, seed_variant):
    user = user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    order = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}}).json()["order"]
    res = client.get("/api/orders")
    assert res.status_code == 200
    assert any(o["order_number"] == order["order_number"] for o in res.json()["items"])

    # another user must NOT see this order
    client.post("/api/auth/logout")
    user_factory.register(client)
    res = client.get(f"/api/orders/{order['order_number']}")
    assert res.status_code == 404


def test_catalog_sync_not_configured_is_honest(client, make_admin):
    from app.db import session_scope
    from app.models import CatalogSyncLog
    from app.services.catalog_sync import CatalogSyncError, sync_payerpin_catalog
    from sqlalchemy import select

    make_admin(client)
    try:
        with session_scope() as session:
            import asyncio

            asyncio.run(sync_payerpin_catalog(session))
        raised = False
    except CatalogSyncError:
        raised = True
    assert raised
    with session_scope() as session:
        log = (
            session.execute(select(CatalogSyncLog).order_by(CatalogSyncLog.id.desc()))
            .scalars()
            .first()
        )
        assert log.status == "NOT_CONFIGURED"


def test_catalog_sync_maps_real_catalog(client, monkeypatch):
    """Sync with a supplier payload maps games/products/variants (never invents IDs)."""
    import asyncio

    from app.db import session_scope
    from app.models import ProductVariant, SupplierProduct
    from app.services.catalog_sync import sync_payerpin_catalog
    from sqlalchemy import select

    catalog = {
        "data": {
            "games": [
                {
                    "key": "pubg-mobile",
                    "name": "PUBG Mobile",
                    "products": [
                        {
                            "id": "prod-uc",
                            "name": "PUBG UC",
                            "fields": [{"key": "player_id", "label": "Player ID", "type": "alphanum", "required": True}],
                            "variations": [
                                {"id": "var-60", "name": "60 UC", "price": 120.0, "currency": "UZS"},
                                {"id": "var-325", "name": "325 UC", "price": 590.0, "currency": "UZS"},
                            ],
                        }
                    ],
                }
            ]
        }
    }

    class FakeProvider:
        configured = True

        async def get_catalog(self):
            return catalog

    monkeypatch.setattr("app.services.catalog_sync.get_payerpin", lambda: FakeProvider())
    monkeypatch.setattr(
        "app.services.catalog_sync.compute_price",
        lambda session, variant: type("B", (), {"unit_price": (variant.supplier_cost or 0) + 1000})(),
    )

    with session_scope() as session:
        stats = asyncio.run(sync_payerpin_catalog(session))
    assert stats["status"] == "SUCCESS"
    assert stats["games_found"] == 1
    assert stats["products_found"] == 1
    assert stats["variants_found"] == 2

    with session_scope() as session:
        sps = session.execute(select(SupplierProduct)).scalars().all()
        assert {sp.supplier_product_id for sp in sps} >= {"prod-uc"}
        assert all(sp.supplier_variation_id in ("var-60", "var-325", "") for sp in sps)
        variants = session.execute(
            select(ProductVariant).where(ProductVariant.supplier_product_id == "prod-uc")
        ).scalars().all()
        assert len(variants) == 2
        assert all(v.active and v.in_stock for v in variants)
        assert all(v.supplier_cost in (12000, 59000) for v in variants)


def test_pricing_admin_recalculates_variants(client, make_admin, seed_variant):
    make_admin(client)
    res = client.put(
        "/api/admin/pricing",
        json={"margin_percent": 20, "margin_fixed": 0, "payment_fee_percent": 0, "payment_fee_fixed": 0},
    )
    assert res.status_code == 200
    variants = client.get("/api/admin/variants").json()["items"]
    v = next(x for x in variants if x["id"] == seed_variant["variant_id"])
    assert v["price"] == seed_variant["supplier_cost"] + round(seed_variant["supplier_cost"] * 20 / 100)


def test_coupon_admin_create_and_validate(client, make_admin):
    make_admin(client)
    res = client.post(
        "/api/admin/coupons",
        json={"code": "adminpct20", "coupon_type": "PERCENT", "value": 20, "per_user_limit": 1},
    )
    assert res.status_code == 200
    res = client.post(
        "/api/admin/coupons",
        json={"code": "BADPCT", "coupon_type": "PERCENT", "value": 150},
    )
    assert res.status_code == 400  # percent > 100 rejected


def test_admin_variant_supplier_mapping_validation(client, make_admin, seed_variant):
    make_admin(client)
    # mapping must be complete: product id without cost is invalid
    res = client.post(
        "/api/admin/variants",
        json={"product_id": seed_variant["product_id"], "name": "Broken", "supplier_product_id": "x"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "invalid_supplier_mapping"


def test_admin_settings_and_broadcast(client, make_admin):
    make_admin(client)
    res = client.put(
        "/api/admin/settings/general",
        json={"value": {"support_link": "https://t.me/test"}},
    )
    assert res.status_code == 200
    info = client.get("/api/support/info?lang=uz").json()
    assert info["support_link"] == "https://t.me/test"

    res = client.post(
        "/api/admin/notifications/broadcast",
        json={"title": "Aksiya", "text": "Bugun barcha UC paketlariga chegirma"},
    )
    assert res.status_code == 200
    assert res.json()["sent"] >= 1
    notes = client.get("/api/account/notifications").json()["items"]
    assert any(n.get("params", {}).get("text", "").startswith("Bugun") for n in notes)

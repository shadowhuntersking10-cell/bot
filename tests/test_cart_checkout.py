"""CART / CHECKOUT / PRICING / COUPONS tests incl. failure scenarios."""
from __future__ import annotations


def test_cart_flow(client, user_factory, seed_variant):
    user_factory.register(client)
    res = client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 2, "player_info": {"player_id": "ABCD1234"}},
    )
    assert res.status_code == 200
    cart = res.json()["cart"]
    assert cart["count"] == 2
    assert cart["valid"] is True
    assert cart["subtotal"] == seed_variant["price"] * 2

    item_id = cart["items"][0]["id"]
    res = client.patch(f"/api/cart/items/{item_id}", json={"quantity": 1})
    assert res.json()["cart"]["count"] == 1

    res = client.del_ = client.delete(f"/api/cart/items/{item_id}")
    assert res.json()["cart"]["count"] == 0


def test_cart_rejects_unmapped_variant(client, user_factory, seed_variant):
    from app.db import session_scope
    from app.models import ProductVariant

    user_factory.register(client)
    with session_scope() as session:
        variant = session.get(ProductVariant, seed_variant["variant_id"])
        variant.in_stock = False
    res = client.post(
        "/api/cart/items", json={"variant_id": seed_variant["variant_id"], "quantity": 1}
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "variant_unavailable"


def test_cart_requires_player_fields(client, user_factory, seed_variant):
    user_factory.register(client)
    res = client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {}},
    )
    assert res.status_code == 400
    assert res.json()["detail"].startswith("missing_field")


def test_invalid_player_id_rejected(client, user_factory, seed_variant):
    user_factory.register(client)
    res = client.post(
        "/api/cart/items",
        json={
            "variant_id": seed_variant["variant_id"],
            "quantity": 1,
            "player_info": {"player_id": "!!!not-alphanum!!!"},
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"].startswith("invalid_field")


def test_checkout_creates_order_with_server_price(client, user_factory, seed_variant):
    user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}})
    assert res.status_code == 200
    data = res.json()
    order = data["order"]
    assert order["order_number"].startswith("VYR-")
    assert order["total"] == seed_variant["price"]  # server price, not frontend
    assert order["status"] == "PENDING_PAYMENT"
    assert data["checkout_url"].startswith("/#/sandbox/checkout")
    # masked player info on the order
    assert "*" in list(order["player_info"].values())[0]


def test_checkout_idempotent(client, user_factory, seed_variant):
    user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res1 = client.post(
        "/api/checkout",
        json={"player_info": {"player_id": "ABCD1234"}, "idempotency_key": "test-idem-key-1"},
    )
    res2 = client.post(
        "/api/checkout",
        json={"player_info": {"player_id": "ABCD1234"}, "idempotency_key": "test-idem-key-1"},
    )
    assert res1.json()["order"]["order_number"] == res2.json()["order"]["order_number"]


def test_price_change_between_cart_and_checkout(client, user_factory, seed_variant):
    """If the price changed server-side, checkout uses the CURRENT price."""
    from app.db import session_scope
    from app.models import ProductVariant
    from sqlalchemy import select

    user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    with session_scope() as session:
        variant = session.execute(
            select(ProductVariant).where(ProductVariant.id == seed_variant["variant_id"])
        ).scalar_one()
        variant.supplier_cost = 15000  # supplier cost changed
        from app.services.pricing import compute_price

        variant.price_amount = compute_price(session, variant).unit_price
        new_price = variant.price_amount
    res = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}})
    assert res.json()["order"]["total"] == new_price
    assert new_price != seed_variant["price"]


def test_pricing_margin_calculation(client, user_factory, seed_variant):
    from app.db import session_scope
    from app.models import AdminSetting, ProductVariant
    from app.services.pricing import compute_price
    from sqlalchemy import select

    user_factory.register(client)
    with session_scope() as session:
        # pin known pricing settings (other tests may mutate global pricing)
        row = session.execute(
            select(AdminSetting).where(AdminSetting.key == "pricing")
        ).scalar_one()
        row.value = {
            "margin_percent": 10,
            "margin_fixed": 0,
            "payment_fee_percent": 0,
            "payment_fee_fixed": 0,
            "min_margin_percent": 0,
            "allow_below_min_margin": False,
        }
    with session_scope() as session:
        variant = session.execute(
            select(ProductVariant).where(ProductVariant.id == seed_variant["variant_id"])
        ).scalar_one()
        breakdown = compute_price(session, variant)
        assert breakdown.unit_price == breakdown.supplier_cost + round(breakdown.supplier_cost * 10 / 100)
        assert breakdown.profit >= 0
        # product-specific margin override
        variant.product.margin_percent = 25
        breakdown2 = compute_price(session, variant)
        assert breakdown2.unit_price == breakdown.supplier_cost + round(breakdown.supplier_cost * 25 / 100)


def test_coupon_percent_and_validation(client, user_factory, seed_variant):
    user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res = client.post("/api/cart/coupon", json={"code": "WELCOME5"})
    assert res.status_code == 200
    assert res.json()["discount"] == round(seed_variant["price"] * 5 / 100)

    res = client.post("/api/cart/coupon", json={"code": "NOPE"})
    assert res.status_code == 400
    assert res.json()["detail"] == "coupon_invalid"


def test_coupon_expired(client, user_factory, seed_variant):
    from datetime import datetime, timedelta

    from app.db import session_scope
    from app.models import CouponCode
    from sqlalchemy import select

    user_factory.register(client)
    with session_scope() as session:
        coupon = CouponCode(code="EXPIRED1", coupon_type="FIXED", value=1000,
                            expires_at=datetime.utcnow() - timedelta(days=1))
        session.add(coupon)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res = client.post("/api/cart/coupon", json={"code": "EXPIRED1"})
    assert res.status_code == 400
    assert res.json()["detail"] == "coupon_expired"


def test_coupon_usage_limit(client, user_factory, seed_variant):
    from app.db import session_scope
    from app.models import CouponCode

    user_factory.register(client)
    with session_scope() as session:
        session.add(
            CouponCode(code="LIMITED1", coupon_type="FIXED", value=500, max_uses=0, used_count=0)
        )
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res = client.post("/api/cart/coupon", json={"code": "LIMITED1"})
    assert res.status_code == 400
    assert res.json()["detail"] == "coupon_exhausted"


def test_empty_cart_checkout_rejected(client, user_factory):
    user_factory.register(client)
    res = client.post("/api/checkout", json={"player_info": {}})
    assert res.status_code == 400
    assert res.json()["detail"] == "cart_empty"


def test_payment_not_configured_honest(monkeypatch, client, user_factory, seed_variant):
    """With no payment provider the checkout must honestly say so."""
    from app.providers.payments.registry import get_payment_provider

    monkeypatch.setattr(
        "app.services.payments.get_payment_provider", lambda: None
    )
    user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}})
    assert res.status_code == 200
    data = res.json()
    assert data["error"] == "payment_not_configured"
    assert data["checkout_url"] is None

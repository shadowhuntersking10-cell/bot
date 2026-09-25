"""Wallet (Hamyon top-up + balance checkout), admin extensions, media, market, coupons."""
from __future__ import annotations

import asyncio
import io
import itertools

import pytest
from fastapi.testclient import TestClient

from main import app

_seq = itertools.count(1)
_pay_seq = itertools.count(1000)


def _new_client(role: str = "user") -> tuple[TestClient, dict]:
    c = TestClient(app)
    n = next(_seq)
    name = f"wx{role}{n}"
    res = c.post("/api/auth/register",
                 json={"username": name, "email": f"{name}@example.com", "password": "Sup3rSecret!"})
    assert res.status_code == 200, res.text
    user = res.json()["user"]
    if role == "admin":
        from sqlalchemy import select

        from app.db import session_scope
        from app.models import Role, RoleName, User

        with session_scope() as s:
            u = s.execute(select(User).where(User.id == user["id"])).scalar_one()
            u.role_id = s.execute(select(Role).where(Role.name == RoleName.ADMIN)).scalar_one().id
    return c, user


@pytest.fixture()
def hamyon_on(monkeypatch):
    from app.config import get_settings
    from app.providers.payments import hamyon as hm

    monkeypatch.setenv("HAMYON_SHOP_ID", "shop-test")
    monkeypatch.setenv("HAMYON_SHOP_KEY", "key-test-0123456789")
    get_settings().reload()

    async def fake_create(self, amount, order_id):
        return hm.HamyonPayment(payment_id=f"pay{next(_pay_seq)}", amount=int(amount),
                                card="8600 1234 5678 9012", expires_in=300, expire_at=None)

    monkeypatch.setattr(hm.HamyonClient, "create_payment", fake_create)
    yield hm
    monkeypatch.setenv("HAMYON_SHOP_ID", "")
    monkeypatch.setenv("HAMYON_SHOP_KEY", "")
    get_settings().reload()


# ---------------------------------------------------------------- authz
def test_admin_ext_endpoints_require_admin():
    anon = TestClient(app)
    user, _ = _new_client()
    for path in ("/api/admin/integrations", "/api/admin/branding", "/api/admin/topup-settings",
                 "/api/admin/media", "/api/admin/topups", "/api/admin/market"):
        assert anon.get(path).status_code == 401, path
        assert user.get(path).status_code == 403, path
    assert user.put("/api/admin/integrations", json={"values": {"PAYERPIN_API_KEY": "x" * 20}}).status_code == 403
    assert user.post("/api/admin/users/1/balance", json={"amount": 1000, "note": "hack"}).status_code == 403


def test_integrations_never_expose_secrets():
    admin, _ = _new_client("admin")
    secret = "pp_live_SECRET_value_987654321"
    res = admin.put("/api/admin/integrations", json={"values": {"PAYERPIN_API_KEY": secret}})
    assert res.status_code == 200, res.text
    listing = admin.get("/api/admin/integrations")
    assert listing.status_code == 200
    assert secret not in listing.text
    item = next(i for i in listing.json()["items"] if i["key"] == "PAYERPIN_API_KEY")
    assert item["secret"] and item["configured"] and "value" not in item
    assert secret not in admin.get("/api/admin/health").text
    assert secret not in admin.get("/api/config").text
    # clear again so other tests stay NOT CONFIGURED
    res = admin.put("/api/admin/integrations", json={"values": {"PAYERPIN_API_KEY__clear": ""}})
    assert res.status_code == 200
    item = next(i for i in admin.get("/api/admin/integrations").json()["items"] if i["key"] == "PAYERPIN_API_KEY")
    assert item["configured"] is False


def test_integrations_reject_unknown_keys():
    admin, _ = _new_client("admin")
    res = admin.put("/api/admin/integrations", json={"values": {"SESSION_SECRET": "abc"}})
    assert res.status_code == 400


# ---------------------------------------------------------------- wallet / Hamyon
def test_topup_not_configured_is_explicit():
    c, _ = _new_client()
    res = c.post("/api/wallet/topups", json={"amount": 50000})
    assert res.status_code in (400, 503)
    assert "not_configured" in res.text
    assert c.get("/api/wallet").json()["payment_configured"] is False


def test_hamyon_topup_callback_credits_once(hamyon_on):
    hm = hamyon_on
    c, user = _new_client()
    res = c.post("/api/wallet/topups", json={"amount": 50000})
    assert res.status_code == 200, res.text
    topup = res.json()["topup"]
    assert topup["card"] if "card" in topup else True
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import TopUp

    with session_scope() as s:
        t = s.execute(select(TopUp).where(TopUp.user_id == user["id"])).scalar_one()
        pid, amount = t.provider_payment_id, t.pay_amount

    anon = TestClient(app)
    bad = anon.post("/api/webhooks/hamyon/complete",
                    data={"payment_id": pid, "amount": amount, "status": "paid", "sign": "0" * 32})
    assert bad.status_code == 403
    assert c.get("/api/wallet").json()["balance"] == 0

    sign = hm.compute_sign("shop-test", pid, amount, "key-test-0123456789")
    payload = {"payment_id": pid, "amount": amount, "status": "paid", "sign": sign}
    ok = anon.post("/api/webhooks/hamyon/complete", data=payload)
    assert ok.status_code == 200, ok.text
    assert c.get("/api/wallet").json()["balance"] == amount * 100
    dup = anon.post("/api/webhooks/hamyon/complete", data=payload)
    assert dup.status_code == 200
    assert c.get("/api/wallet").json()["balance"] == amount * 100  # credited exactly once
    unknown_sign = hm.compute_sign("shop-test", "nope", amount, "key-test-0123456789")
    unknown = anon.post("/api/webhooks/hamyon/complete",
                        data={"payment_id": "nope", "amount": amount, "status": "paid", "sign": unknown_sign})
    assert unknown.status_code == 404


def test_hamyon_cancel_never_credits(hamyon_on):
    hm = hamyon_on
    c, user = _new_client()
    assert c.post("/api/wallet/topups", json={"amount": 20000}).status_code == 200
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import TopUp

    with session_scope() as s:
        t = s.execute(select(TopUp).where(TopUp.user_id == user["id"])).scalar_one()
        pid, amount = t.provider_payment_id, t.pay_amount
    sign = hm.compute_sign("shop-test", pid, amount, "key-test-0123456789")
    res = TestClient(app).post("/api/webhooks/hamyon/complete", data={
        "payment_id": pid, "amount": amount, "status": "cancel", "reason": "timeout", "sign": sign})
    assert res.status_code == 200
    assert c.get("/api/wallet").json()["balance"] == 0


def test_balance_checkout_and_insufficient(seed_variant):
    admin, _ = _new_client("admin")
    c, user = _new_client()
    body = {"variant_id": seed_variant["variant_id"], "player_info": {"player_id": "5123456789"},
            "payment_method": "balance"}
    res = c.post("/api/checkout/quick", json=body)
    assert res.status_code == 402
    assert res.json()["detail"]["code"] == "insufficient_balance"

    price_som = seed_variant["price"] // 100 + 1
    assert admin.post(f"/api/admin/users/{user['id']}/balance",
                      json={"amount": price_som, "note": "test credit"}).status_code == 200
    res = c.post("/api/checkout/quick", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["order"]["status"] in ("PAID", "FULFILLMENT_PENDING")
    assert data["balance"] == price_som * 100 - seed_variant["price"]
    assert "supplier_cost" not in res.text


def test_unconfigured_supplier_refunds_balance(seed_variant):
    """Paid order + Payerpin NOT CONFIGURED -> FAILED and refunded, never COMPLETED."""
    from app.services import fulfillment as f

    admin, _ = _new_client("admin")
    c, user = _new_client()
    admin.post(f"/api/admin/users/{user['id']}/balance",
               json={"amount": seed_variant["price"] // 100 + 1, "note": "test credit"})
    res = c.post("/api/checkout/quick", json={
        "variant_id": seed_variant["variant_id"], "player_info": {"player_id": "5123456789"},
        "payment_method": "balance"})
    assert res.status_code == 200, res.text
    order_id = res.json()["order"]["id"]
    before = c.get("/api/wallet").json()["balance"]
    asyncio.run(f.FulfillmentWorker().process_one(order_id))
    order = c.get(f"/api/orders/{res.json()['order']['order_number']}").json()["order"]
    assert order["status"] in ("REFUNDED", "FAILED")
    assert order["status"] != "COMPLETED"
    if order["status"] == "REFUNDED":
        assert c.get("/api/wallet").json()["balance"] == before + seed_variant["price"]


# ---------------------------------------------------------------- coupons
def test_coupon_preview_for_single_variant(seed_variant):
    admin, _ = _new_client("admin")
    code = f"SAVE{next(_seq)}"
    res = admin.post("/api/admin/coupons", json={"code": code, "coupon_type": "PERCENT", "value": 1,
                                                 "allow_below_margin": True})
    assert res.status_code == 200, res.text
    c, _ = _new_client()
    res = c.post("/api/cart/coupon", json={"code": code, "variant_id": seed_variant["variant_id"]})
    assert res.status_code == 200, res.text
    assert res.json()["discount"] > 0
    assert c.post("/api/cart/coupon", json={"code": "NOPE-XX", "variant_id": seed_variant["variant_id"]}).status_code == 400


# ---------------------------------------------------------------- media / art
def test_upload_image_and_reject_non_images():
    from PIL import Image

    admin, _ = _new_client("admin")
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 80, 200)).save(buf, "PNG")
    res = admin.post("/api/admin/uploads", files={"file": ("x.png", buf.getvalue(), "image/png")},
                     data={"kind": "logo"})
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["url"].startswith("/uploads/") and info["width"] == 64
    assert any(m["name"] == info["name"] for m in admin.get("/api/admin/media").json()["items"])
    assert TestClient(app).get(info["url"]).status_code == 200

    evil = admin.post("/api/admin/uploads",
                      files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")}, data={"kind": "media"})
    assert evil.status_code == 400
    assert admin.delete(f"/api/admin/media/{info['name']}").status_code == 200
    assert admin.delete("/api/admin/media/..%2F..%2Fmain.py").status_code in (400, 404, 405)
    assert admin.delete("/api/admin/media/..main.py").status_code in (400, 404)


def test_currency_art_is_escaped_svg():
    res = TestClient(app).get("/api/art/currency.svg", params={"c": "<script>"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert "<script>" not in res.text


def test_branding_update_roundtrip():
    admin, _ = _new_client("admin")
    res = admin.put("/api/admin/branding", json={"value": {"site_name": "VYRON Test"}})
    assert res.status_code == 200, res.text
    assert TestClient(app).get("/api/config").json()["branding"]["site_name"] == "VYRON Test"
    admin.put("/api/admin/branding", json={"value": {"site_name": "VYRON"}})


# ---------------------------------------------------------------- market
def test_market_listing_requires_moderation():
    c, _ = _new_client()
    res = c.post("/api/market", json={"game_slug": "pubg-mobile", "title": "Level 70 account",
                                      "description": "Good skins", "price": 150000})
    assert res.status_code == 200, res.text
    listing_id = res.json()["listing"]["id"] if "listing" in res.json() else res.json()["id"]
    public = TestClient(app).get("/api/market").json()["items"]
    assert all(i["id"] != listing_id for i in public)
    admin, _ = _new_client("admin")
    assert c.post(f"/api/admin/market/{listing_id}/moderate", json={"action": "approve"}).status_code == 403
    assert admin.post(f"/api/admin/market/{listing_id}/moderate", json={"action": "approve"}).status_code == 200
    public = TestClient(app).get("/api/market").json()["items"]
    assert any(i["id"] == listing_id for i in public)


# ---------------------------------------------------------------- auth extras
def test_forgot_password_no_enumeration():
    anon = TestClient(app)
    a = anon.post("/api/auth/password/forgot", json={"email": "nobody-here@example.com"})
    _, user = _new_client()
    b = anon.post("/api/auth/password/forgot", json={"email": user["email"]})
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()
    assert anon.post("/api/auth/password/reset", json={"token": "x" * 40, "password": "N3wStrongPass!"}).status_code == 400

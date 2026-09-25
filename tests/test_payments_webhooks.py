"""PAYMENTS / WEBHOOKS / IDEMPOTENCY tests incl. security failure scenarios."""
from __future__ import annotations

import json


def _create_order_with_payment(client, user_factory, seed_variant):
    user_factory.register(client)
    client.post(
        "/api/cart/items",
        json={"variant_id": seed_variant["variant_id"], "quantity": 1, "player_info": {"player_id": "ABCD1234"}},
    )
    res = client.post("/api/checkout", json={"player_info": {"player_id": "ABCD1234"}})
    data = res.json()
    assert data["checkout_url"], "sandbox payment must be configured in tests"
    return data["order"]


def _sandbox_event(order, amount, status="succeeded"):
    from app.providers.payments.sandbox import SandboxPaymentProvider

    return SandboxPaymentProvider.build_event(
        f"sandbox_pay_test_{order['id']}", order["order_number"], amount, order["currency"], status
    )


def test_payment_webhook_marks_paid_and_fulfills(client, user_factory, seed_variant):
    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"])
    res = client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "processed"

    detail = client.get(f"/api/orders/{order['order_number']}").json()["order"]
    assert detail["status"] in ("PAID", "FULFILLMENT_PENDING")
    assert detail["payment_status"] == "PAID"


def test_webhook_invalid_signature_rejected(client, user_factory, seed_variant):
    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"])
    res = client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": "0" * 64,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    assert res.status_code == 400
    detail = client.get(f"/api/orders/{order['order_number']}").json()["order"]
    assert detail["status"] == "PENDING_PAYMENT"  # never fulfilled


def test_webhook_duplicate_delivery_idempotent(client, user_factory, seed_variant):
    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"])
    headers = {
        "Content-Type": "application/json",
        "X-Payment-Signature": sig,
        "X-Payment-Timestamp": ts,
        "X-Payment-Delivery-Id": delivery,
    }
    res1 = client.post("/api/webhooks/payments", content=raw, headers=headers)
    res2 = client.post("/api/webhooks/payments", content=raw, headers=headers)
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res2.json()["status"] in ("duplicate", "already_paid")


def test_webhook_amount_mismatch_rejected(client, user_factory, seed_variant):
    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"] + 100)
    res = client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "amount_mismatch"


def test_webhook_currency_mismatch_rejected(client, user_factory, seed_variant):
    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"])
    raw = raw.replace(b'"currency":"UZS"', b'"currency":"USD"')
    from app.providers.payments.sandbox import SandboxPaymentProvider

    ts, sig = SandboxPaymentProvider.sign_webhook(raw)  # valid sig over bad payload
    res = client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery + "c",
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "currency_mismatch"


def test_webhook_unknown_order_rejected(client):
    from app.providers.payments.sandbox import SandboxPaymentProvider

    raw, ts, sig, delivery = SandboxPaymentProvider.build_event(
        "unknown_payment_id", "VYR-19990101-999999", 100, "UZS"
    )
    res = client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    assert res.status_code == 404


def test_webhook_failed_payment_never_fulfills(client, user_factory, seed_variant):
    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"], status="failed")
    res = client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    assert res.status_code == 200
    detail = client.get(f"/api/orders/{order['order_number']}").json()["order"]
    assert detail["status"] == "FAILED"
    assert detail["fulfillment_status"] in (None, "PENDING")


def test_sandbox_complete_end_to_end(client, user_factory, seed_variant):
    """Full provider cycle: checkout -> sandbox page -> signed webhook -> paid."""
    order = _create_order_with_payment(client, user_factory, seed_variant)
    from app.db import session_scope
    from app.models import Payment
    from sqlalchemy import select

    with session_scope() as session:
        payment = session.execute(
            select(Payment).where(Payment.order_id == order["id"])
        ).scalars().first()
        provider_payment_id = payment.provider_payment_id

    res = client.post(
        "/sandbox/complete",
        json={
            "payment_id": provider_payment_id,
            "order_number": order["order_number"],
            "amount": order["total"],
            "currency": order["currency"],
            "status": "succeeded",
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "processed"


def test_fulfillment_idempotency_single_supplier_tx(client, user_factory, seed_variant):
    """Paid order produces exactly ONE supplier transaction (no duplicate top-ups)."""
    from app.db import session_scope
    from app.models import Fulfillment, FulfillmentStatus, Order, SupplierTransaction
    from app.services.fulfillment import FulfillmentService
    from sqlalchemy import select

    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"])
    client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    service = FulfillmentService()
    with session_scope() as session:
        db_order = session.execute(
            select(Order).where(Order.id == order["id"])
        ).scalar_one()
        order_idem = db_order.idempotency_key
        # process twice — must create only one supplier transaction
        service.process_order(session, db_order.id)
        first = session.info.get("pending_supplier_call")
        session.expire_all()
    with session_scope() as session:
        service.process_order(session, order["id"])
    with session_scope() as session:
        txs = (
            session.execute(
                select(SupplierTransaction).where(SupplierTransaction.order_id == order["id"])
            )
            .scalars()
            .all()
        )
        assert len(txs) == 1
        assert txs[0].idempotency_key == f"sup:{order_idem}"


def test_payment_transaction_and_event_records(client, user_factory, seed_variant):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import PaymentTransaction, WebhookEvent

    order = _create_order_with_payment(client, user_factory, seed_variant)
    raw, ts, sig, delivery = _sandbox_event(order, order["total"])
    client.post(
        "/api/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Payment-Signature": sig,
            "X-Payment-Timestamp": ts,
            "X-Payment-Delivery-Id": delivery,
        },
    )
    with session_scope() as session:
        events = (
            session.execute(select(WebhookEvent).where(WebhookEvent.provider == "sandbox"))
            .scalars()
            .all()
        )
        assert any(e.delivery_id == delivery and e.signature_valid for e in events)
        txs = session.execute(select(PaymentTransaction)).scalars().all()
        assert any(t.kind == "WEBHOOK" for t in txs)

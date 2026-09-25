"""Payment service: create payments, verify webhooks idempotently.

Only a verified server-side webhook can authorize fulfillment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    PaymentTransaction,
    WebhookEvent,
)
from app.providers.payments.base import PaymentNotConfigured
from app.providers.payments.registry import get_payment_provider
from app.security import new_idempotency_key
from app.services.orders import mark_order_paid

log = get_logger("vyron.payments")


class PaymentError(Exception):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(key)
        self.key = key
        self.detail = detail


async def create_payment_for_order(
    session: Session,
    order: Order,
    *,
    return_url: str,
    description: str,
    idempotency_key: Optional[str] = None,
) -> Payment:
    provider = get_payment_provider()
    if provider is None or not provider.configured():
        raise PaymentError("payment_not_configured")

    idem = idempotency_key or f"pay:{order.idempotency_key}"
    existing = session.execute(
        select(Payment).where(Payment.idempotency_key == idem)
    ).scalar_one_or_none()
    if existing is not None and existing.status == PaymentStatus.PENDING:
        return existing

    created = await provider.create_payment(
        idempotency_key=idem,
        amount=order.total_amount,
        currency=order.currency,
        order_number=order.order_number,
        description=description,
        return_url=return_url,
    )
    payment = Payment(
        order_id=order.id,
        provider=provider.code,
        provider_payment_id=created.provider_payment_id,
        idempotency_key=idem,
        amount=order.total_amount,
        currency=order.currency,
        status=PaymentStatus.PENDING,
        checkout_url=created.checkout_url,
    )
    session.add(payment)
    session.flush()
    session.add(
        PaymentTransaction(
            payment_id=payment.id,
            kind="CREATE",
            provider_event_id=None,
            status="CREATED",
            payload={"provider": provider.code, "payment_id": created.provider_payment_id},
        )
    )
    log.info("payment created order=%s provider=%s", order.order_number, provider.code)
    return payment


def _register_webhook_delivery(
    session: Session, provider_code: str, delivery_id: str, event_type: Optional[str],
    payload: Optional[dict], signature_valid: bool,
) -> WebhookEvent:
    existing = session.execute(
        select(WebhookEvent).where(
            WebhookEvent.provider == provider_code,
            WebhookEvent.delivery_id == delivery_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing  # duplicate delivery
    event = WebhookEvent(
        provider=provider_code,
        delivery_id=delivery_id,
        event_type=event_type,
        payload=payload,
        signature_valid=signature_valid,
    )
    session.add(event)
    session.flush()
    return event


def process_payment_webhook(
    session: Session,
    *,
    raw_body: bytes,
    headers: dict,
    payload: dict,
) -> dict:
    """Verify + process a payment webhook. Idempotent on delivery_id and payment state."""
    provider = get_payment_provider()
    if provider is None or not provider.configured():
        raise PaymentError("payment_not_configured")

    signature_valid = provider.verify_webhook(raw_body, headers)
    delivery_id = (
        headers.get("x-payment-delivery-id")
        or payload.get("delivery_id")
        or f"auto:{__import__('hashlib').sha256(raw_body).hexdigest()[:40]}"
    )
    event_type = payload.get("event")
    event = _register_webhook_delivery(
        session, provider.code, str(delivery_id), event_type,
        {"payment": payload.get("payment")}, signature_valid,
    )
    if not signature_valid:
        log.warning("payment webhook invalid signature delivery=%s", delivery_id)
        raise PaymentError("invalid_signature")
    if event.processed:
        return {"status": "duplicate", "delivery_id": event.delivery_id}

    payment_data = payload.get("payment") or {}
    provider_payment_id = payment_data.get("id")
    status = str(payment_data.get("status", "")).lower()
    amount = payment_data.get("amount")
    currency = str(payment_data.get("currency", "")).upper()
    order_number = payment_data.get("order_number")

    payment = None
    if provider_payment_id:
        payment = session.execute(
            select(Payment).where(Payment.provider_payment_id == str(provider_payment_id))
        ).scalar_one_or_none()
    if payment is None and order_number:
        order = session.execute(
            select(Order).where(Order.order_number == order_number)
        ).scalar_one_or_none()
        if order is not None and order.payments:
            payment = order.payments[-1]
    if payment is None:
        log.warning("payment webhook unknown payment delivery=%s", delivery_id)
        raise PaymentError("unknown_order")

    order = payment.order

    # verify amount / currency
    if amount is not None and int(amount) != payment.amount:
        log.warning("payment webhook amount mismatch payment=%s", payment.id)
        raise PaymentError("amount_mismatch")
    if currency and currency != payment.currency:
        raise PaymentError("currency_mismatch")
    if payment.status == PaymentStatus.PAID:
        event.processed = True
        event.processed_at = datetime.utcnow()
        return {"status": "already_paid", "order_number": order.order_number}

    session.add(
        PaymentTransaction(
            payment_id=payment.id,
            kind="WEBHOOK",
            provider_event_id=str(delivery_id),
            status="RECEIVED",
            payload={"event": event_type, "payment": payment_data},
        )
    )

    if status in ("succeeded", "paid", "success", "completed"):
        payment.status = PaymentStatus.PAID
        payment.paid_at = datetime.utcnow()
        mark_order_paid(session, order)
    elif status in ("failed", "cancelled", "canceled"):
        payment.status = PaymentStatus.FAILED if status == "failed" else PaymentStatus.CANCELLED
        if order.status == OrderStatus.PENDING_PAYMENT:
            order.status = OrderStatus.FAILED if status == "failed" else OrderStatus.CANCELLED
    elif status == "refunded":
        payment.status = PaymentStatus.REFUNDED
        order.status = OrderStatus.REFUNDED
    else:
        # Unknown provider statuses never authorize anything.
        log.warning("payment webhook unknown status=%s", status)
        return {"status": "ignored_unknown_status"}

    event.processed = True
    event.processed_at = datetime.utcnow()
    log.info("payment webhook processed order=%s status=%s", order.order_number, status)
    return {"status": "processed", "order_number": order.order_number, "payment_status": payment.status.value}

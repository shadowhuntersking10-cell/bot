"""Webhook endpoints: payment provider + Payerpin supplier events.

Both endpoints are idempotent (delivery IDs stored), signature-verified
(HMAC-SHA256, constant-time compare), and use the ORIGINAL raw body.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging_config import get_logger
from app.models import (
    Fulfillment,
    FulfillmentStatus,
    Order,
    OrderStatus,
    Supplier,
    SupplierTransaction,
    WebhookEvent,
)
from app.providers.suppliers.payerpin import get_payerpin, map_supplier_status
from app.security import verify_hmac_signature
from app.services.fulfillment import FulfillmentService
from app.services.payments import PaymentError, process_payment_webhook

log = get_logger("vyron.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/payments")
async def payment_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_payment_delivery_id: Optional[str] = Header(default=None),
):
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        result = process_payment_webhook(
            db,
            raw_body=raw,
            headers=headers,
            payload=payload,
        )
        db.commit()
        return result
    except PaymentError as exc:
        db.rollback()
        status = 400 if exc.key in ("invalid_signature", "amount_mismatch", "currency_mismatch") else 404
        if exc.key == "payment_not_configured":
            status = 503
        raise HTTPException(status_code=status, detail=exc.key)


@router.post("/payerpin")
async def payerpin_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_payerpin_signature: Optional[str] = Header(default=None),
    x_payerpin_timestamp: Optional[str] = Header(default=None),
    x_payerpin_delivery_id: Optional[str] = Header(default=None),
):
    """Payerpin fulfillment events:
    order.completed / order.failed / order.refunded / payment.succeeded

    Signature: HMAC-SHA256 over `timestamp + '.' + rawBody`.
    """
    from app.config import get_settings

    raw = await request.body()
    settings = get_settings()
    # dedicated supplier webhook secret (falls back to the shared webhook secret)
    secret = settings.payerpin_webhook_secret or settings.payment_webhook_secret
    import hashlib

    delivery_id = x_payerpin_delivery_id or f"auto:{hashlib.sha256(raw).hexdigest()[:40]}"

    existing = db.execute(
        select(WebhookEvent).where(
            WebhookEvent.provider == "payerpin",
            WebhookEvent.delivery_id == str(delivery_id),
        )
    ).scalar_one_or_none()
    if existing is not None and existing.processed:
        return {"status": "duplicate"}

    signature_valid = verify_hmac_signature(
        secret, raw, x_payerpin_timestamp or "", x_payerpin_signature or ""
    )
    event = existing or WebhookEvent(
        provider="payerpin",
        delivery_id=str(delivery_id),
        signature_valid=signature_valid,
        payload=None,
    )
    if existing is None:
        db.add(event)
        db.flush()

    if not signature_valid:
        log.warning("payerpin webhook invalid signature delivery=%s", delivery_id)
        raise HTTPException(status_code=400, detail="invalid_signature")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    event_type = payload.get("event") or payload.get("type")
    event.event_type = event_type
    event.payload = payload

    data = payload.get("data") or payload.get("order") or payload
    supplier_order_id = str(
        data.get("id") or data.get("orderId") or data.get("order_id") or ""
    )
    raw_status = data.get("status")
    status = map_supplier_status(raw_status)

    tx = None
    if supplier_order_id:
        tx = db.execute(
            select(SupplierTransaction).where(
                SupplierTransaction.supplier_order_id == supplier_order_id
            )
        ).scalars().first()
    if tx is None:
        reference = data.get("reference") or data.get("clientReference")
        if reference:
            order = db.execute(
                select(Order).where(Order.order_number == str(reference))
            ).scalar_one_or_none()
            if order is not None and order.items:
                tx = db.execute(
                    select(SupplierTransaction).where(SupplierTransaction.order_id == order.id)
                ).scalars().first()

    if tx is None:
        event.processed = True
        event.processed_at = datetime.utcnow()
        log.warning("payerpin webhook unknown order delivery=%s", delivery_id)
        return {"status": "ignored_unknown_order"}

    service = FulfillmentService()
    if event_type in ("order.refunded",):
        service.record_supplier_result(
            db, tx.id, supplier_order_id=tx.supplier_order_id,
            status="FAILED", raw={"event": event_type, "data": data},
            error_code="REFUNDED", error_message="Supplier reported refund",
        )
        tx.order.status = OrderStatus.REFUNDED
    elif status == "COMPLETED" or event_type == "order.completed":
        service.record_supplier_result(
            db, tx.id, supplier_order_id=tx.supplier_order_id or supplier_order_id,
            status="COMPLETED", raw={"event": event_type, "data": data},
        )
    elif status == "FAILED" or event_type == "order.failed":
        service.record_supplier_result(
            db, tx.id, supplier_order_id=tx.supplier_order_id or supplier_order_id,
            status="FAILED", raw={"event": event_type, "data": data},
            error_code="SUPPLIER_FAILED", error_message="Supplier reported failure",
        )
    else:
        # Unknown statuses never become COMPLETED.
        service.record_supplier_result(
            db, tx.id, supplier_order_id=tx.supplier_order_id or supplier_order_id,
            status="PROCESSING", raw={"event": event_type, "data": data},
        )

    event.processed = True
    event.processed_at = datetime.utcnow()
    db.commit()
    return {"status": "processed"}


# ---------------------------------------------------------------------------
# Hamyon API callbacks (wallet top-ups)
#   prepare_url  -> {PUBLIC_BASE_URL}/api/webhooks/hamyon/prepare
#   complete_url -> {PUBLIC_BASE_URL}/api/webhooks/hamyon/complete
# sign = md5(shop_id + payment_id + amount + shop_key)
# ---------------------------------------------------------------------------
async def _read_callback_payload(request: Request) -> dict:
    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    data: dict = {}
    if "application/json" in content_type:
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
            data = parsed if isinstance(parsed, dict) else {}
        except Exception:
            data = {}
    else:
        from urllib.parse import parse_qsl

        data = dict(parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True))
        if not data and raw.strip().startswith(b"{"):
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = {}
    # query-string fallback (some senders use GET-style params)
    for key, value in request.query_params.items():
        data.setdefault(key, value)
    return data


def _safe_payload(data: dict) -> dict:
    return {k: v for k, v in data.items() if "key" not in str(k).lower()}


def _hamyon_topup(db: Session, payment_id: str, order_id: Optional[str]):
    from app.models import TopUp

    topup = None
    if payment_id:
        topup = db.execute(
            select(TopUp).where(TopUp.provider == "hamyon", TopUp.provider_payment_id == payment_id)
        ).scalar_one_or_none()
    if topup is None and order_id:
        topup = db.execute(select(TopUp).where(TopUp.reference == order_id)).scalar_one_or_none()
        if topup is not None and topup.provider_payment_id and topup.provider_payment_id != payment_id:
            return None  # never let a different payment settle this top-up
    return topup


@router.post("/hamyon/complete")
async def hamyon_complete(request: Request, db: Session = Depends(get_db)):
    from app.providers.payments.hamyon import get_hamyon, normalize_status
    from app.services.wallet import WalletError, mark_topup_cancelled, mark_topup_paid

    hamyon = get_hamyon()
    if not hamyon.configured():
        raise HTTPException(status_code=503, detail="payment_not_configured")
    data = await _read_callback_payload(request)
    payment_id = str(data.get("payment_id") or "").strip()
    amount_raw = data.get("amount")
    status_raw = data.get("status")
    signature_valid = hamyon.verify_sign(payment_id, amount_raw, data.get("sign"))

    delivery_id = f"complete:{payment_id}:{str(status_raw or '').lower()}"[:128]
    event = db.execute(
        select(WebhookEvent).where(
            WebhookEvent.provider == "hamyon", WebhookEvent.delivery_id == delivery_id
        )
    ).scalar_one_or_none()
    if event is not None and event.processed:
        return {"ok": True, "status": "duplicate"}
    if event is None:
        event = WebhookEvent(
            provider="hamyon",
            delivery_id=delivery_id,
            event_type=f"payment.{str(status_raw or 'unknown').lower()}"[:64],
            signature_valid=signature_valid,
            payload=_safe_payload(data),
        )
        db.add(event)
        db.flush()
    if not signature_valid:
        db.commit()
        log.warning("hamyon callback invalid signature payment=%s", payment_id)
        raise HTTPException(status_code=403, detail="invalid_signature")

    topup = _hamyon_topup(db, payment_id, data.get("order_id"))
    if topup is None:
        db.commit()
        log.warning("hamyon callback unknown payment=%s", payment_id)
        raise HTTPException(status_code=404, detail="unknown_payment")

    status = normalize_status(status_raw)
    result = "ignored"
    try:
        if status == "PAID":
            amount = int(float(amount_raw))
            mark_topup_paid(db, topup, amount, source="callback")
            result = "paid"
        elif status == "CANCELLED":
            mark_topup_cancelled(db, topup, str(data.get("reason") or "cancel"))
            result = "cancelled"
    except (WalletError, TypeError, ValueError) as exc:
        db.rollback()
        key = getattr(exc, "key", "invalid_amount")
        log.warning("hamyon callback rejected payment=%s reason=%s", payment_id, key)
        raise HTTPException(status_code=400, detail=key)
    event.processed = True
    event.processed_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "status": result}


@router.post("/hamyon/prepare")
async def hamyon_prepare(request: Request, db: Session = Depends(get_db)):
    """Payment started notification — informational only (never credits)."""
    from app.providers.payments.hamyon import get_hamyon

    hamyon = get_hamyon()
    if not hamyon.configured():
        raise HTTPException(status_code=503, detail="payment_not_configured")
    data = await _read_callback_payload(request)
    payment_id = str(data.get("payment_id") or "").strip()
    if data.get("sign") and not hamyon.verify_sign(payment_id, data.get("amount"), data.get("sign")):
        raise HTTPException(status_code=403, detail="invalid_signature")
    log.info("hamyon prepare payment=%s", payment_id)
    return {"ok": True}

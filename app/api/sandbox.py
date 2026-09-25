"""SANDBOX payment pages — explicit test-mode provider (PAYMENT_PROVIDER=sandbox).

The sandbox behaves like a REAL external provider: it signs webhooks with
HMAC-SHA256 over `timestamp + '.' + rawBody` and posts them to VYRON's real
payment webhook endpoint where signatures are verified. It is never enabled
unless explicitly configured and is clearly labelled in the UI.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.logging_config import get_logger
from app.providers.payments.sandbox import SandboxPaymentProvider
from app.services.payments import PaymentError, process_payment_webhook

log = get_logger("vyron.sandbox")

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


class CompleteBody(BaseModel):
    payment_id: str
    order_number: str
    amount: int
    currency: str = "UZS"
    status: str = "succeeded"


@router.post("/complete")
def complete(body: CompleteBody, db: Session = Depends(get_db)):
    """Provider-side 'charge' + signed webhook delivery to VYRON's own endpoint."""
    settings = get_settings()
    if settings.payment_provider != "sandbox":
        raise HTTPException(status_code=404, detail="not_found")

    raw, timestamp, signature, delivery_id = SandboxPaymentProvider.build_event(
        body.payment_id, body.order_number, body.amount, body.currency, body.status
    )
    payload = json.loads(raw.decode("utf-8"))
    headers = {
        "x-payment-signature": signature,
        "x-payment-timestamp": timestamp,
        "x-payment-delivery-id": delivery_id,
    }
    try:
        result = process_payment_webhook(
            db, raw_body=raw, headers=headers, payload=payload
        )
        db.commit()
        return result
    except PaymentError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.key)

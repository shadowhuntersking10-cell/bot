"""Generic signed-webhook payment provider.

Adapter for a real payment provider that verifies webhooks with
HMAC-SHA256 over `timestamp + '.' + rawBody` (headers):
    X-Payment-Signature
    X-Payment-Timestamp
    X-Payment-Delivery-Id

Configure with:
    PAYMENT_PROVIDER=generic
    PAYMENT_API_KEY=...        (server-only)
    PAYMENT_SECRET=...         (server-only)
    PAYMENT_WEBHOOK_SECRET=... (server-only, used for webhook verification)

create_payment returns NOT CONFIGURED behavior until the provider's create
endpoint is confirmed — we never invent provider endpoints or fake success.
"""
from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.providers.payments.base import (
    CreatedPayment,
    PaymentNotConfigured,
    PaymentProvider,
)
from app.security import verify_hmac_signature


class GenericPaymentProvider(PaymentProvider):
    code = "generic"

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.payment_api_key and s.payment_webhook_secret)

    async def create_payment(
        self,
        *,
        idempotency_key: str,
        amount: int,
        currency: str,
        order_number: str,
        description: str,
        return_url: str,
    ) -> CreatedPayment:
        # Provider create-endpoint must be confirmed against the provider's
        # official documentation before enabling. Never invent endpoints.
        raise PaymentNotConfigured(
            "Payment provider create endpoint NOT CONFIGURED. "
            "Set PAYMENT_PROVIDER to a configured adapter."
        )

    def verify_webhook(self, raw_body: bytes, headers: dict) -> bool:
        secret = get_settings().payment_webhook_secret
        signature = headers.get("x-payment-signature", "")
        timestamp = headers.get("x-payment-timestamp", "")
        return verify_hmac_signature(secret, raw_body, timestamp, signature)

    async def refund(self, provider_payment_id: str, amount: int) -> dict:
        raise PaymentNotConfigured("Refund endpoint NOT CONFIGURED")

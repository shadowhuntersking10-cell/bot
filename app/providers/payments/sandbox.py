"""SANDBOX payment provider — explicit test-mode integration harness.

ONLY active when the owner explicitly sets:
    PAYMENT_PROVIDER=sandbox
    PAYMENT_WEBHOOK_SECRET=<random>

It performs a REAL integration cycle against VYRON's own payment webhook:
provider-side checkout page -> signed HMAC webhook (timestamp + '.' + rawBody)
-> server-side verification -> fulfillment. It is clearly labelled SANDBOX in
the UI and is never enabled by default — production must configure a real
payment provider.
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Optional
from urllib.parse import quote

from app.config import get_settings
from app.providers.payments.base import (
    CreatedPayment,
    PaymentNotConfigured,
    PaymentProvider,
)
from app.security import verify_hmac_signature


class SandboxPaymentProvider(PaymentProvider):
    code = "sandbox"

    def configured(self) -> bool:
        return bool(get_settings().payment_webhook_secret)

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
        if not self.configured():
            raise PaymentNotConfigured()
        provider_payment_id = f"sandbox_pay_{secrets.token_hex(8)}"
        params = (
            f"payment_id={quote(provider_payment_id)}"
            f"&order_number={quote(order_number)}"
            f"&amount={amount}&currency={quote(currency)}"
            f"&return_url={quote(return_url)}"
            f"&description={quote(description)}"
        )
        return CreatedPayment(
            provider_payment_id=provider_payment_id,
            checkout_url=f"/#/sandbox/checkout?{params}",
            raw={"provider": "sandbox", "payment_id": provider_payment_id},
        )

    def verify_webhook(self, raw_body: bytes, headers: dict) -> bool:
        secret = get_settings().payment_webhook_secret
        signature = headers.get("x-payment-signature", "")
        timestamp = headers.get("x-payment-timestamp", "")
        return verify_hmac_signature(secret, raw_body, timestamp, signature)

    async def refund(self, provider_payment_id: str, amount: int) -> dict:
        return {"status": "refunded", "provider_payment_id": provider_payment_id}

    # ---- provider-side signing (used by the sandbox checkout page) ----------
    @staticmethod
    def sign_webhook(raw_body: bytes) -> tuple[str, str]:
        """Produce (timestamp, signature) exactly as a real provider would."""
        import hashlib
        import hmac as hmac_mod

        secret = get_settings().payment_webhook_secret
        timestamp = str(int(time.time()))
        signed = timestamp.encode("utf-8") + b"." + raw_body
        signature = hmac_mod.new(
            secret.encode("utf-8"), signed, hashlib.sha256
        ).hexdigest()
        return timestamp, signature

    @staticmethod
    def build_event(payment_id: str, order_number: str, amount: int, currency: str,
                    status: str = "succeeded") -> tuple[bytes, str, str, str]:
        event_id = f"sandbox_evt_{secrets.token_hex(8)}"
        event = {
            "event": "payment.succeeded" if status == "succeeded" else "payment.failed",
            "delivery_id": event_id,
            "payment": {
                "id": payment_id,
                "status": status,
                "amount": amount,
                "currency": currency,
                "order_number": order_number,
                "merchant": "sandbox",
            },
        }
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        timestamp, signature = SandboxPaymentProvider.sign_webhook(body)
        return body, timestamp, signature, event_id

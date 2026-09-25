"""Payment provider interface.

Flow: CUSTOMER -> VYRON -> PAYMENT PROVIDER -> verified payment webhook
      -> VYRON BACKEND -> PAYERPIN -> game top-up.

A frontend "payment successful" message is NEVER proof of payment.
Only a verified server-side webhook authorizes fulfillment.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


class PaymentNotConfigured(Exception):
    """Raised when no payment provider is configured (honest failure)."""


class PaymentError(Exception):
    def __init__(self, message: str, code: str = "PAYMENT_ERROR"):
        super().__init__(message)
        self.safe_message = message
        self.code = code


@dataclass
class CreatedPayment:
    provider_payment_id: Optional[str]
    checkout_url: Optional[str]
    raw: dict = field(default_factory=dict)


class PaymentProvider(abc.ABC):
    code: str = "base"

    @abc.abstractmethod
    def configured(self) -> bool: ...

    @abc.abstractmethod
    async def create_payment(
        self,
        *,
        idempotency_key: str,
        amount: int,
        currency: str,
        order_number: str,
        description: str,
        return_url: str,
    ) -> CreatedPayment: ...

    @abc.abstractmethod
    def verify_webhook(self, raw_body: bytes, headers: dict) -> bool: ...

    @abc.abstractmethod
    async def refund(self, provider_payment_id: str, amount: int) -> dict: ...

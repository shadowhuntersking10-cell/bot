"""Payment provider registry."""
from __future__ import annotations

from app.config import get_settings
from app.providers.payments.base import PaymentProvider
from app.providers.payments.generic import GenericPaymentProvider
from app.providers.payments.sandbox import SandboxPaymentProvider

_PROVIDERS: dict[str, PaymentProvider] = {
    "sandbox": SandboxPaymentProvider(),
    "generic": GenericPaymentProvider(),
}


def get_payment_provider() -> PaymentProvider | None:
    settings = get_settings()
    if not settings.payment_provider:
        return None
    return _PROVIDERS.get(settings.payment_provider)

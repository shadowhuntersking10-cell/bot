"""Hamyon API adapter — automatic HUMO / UZCARD card payments.

Official documentation: https://hamyon-api.uz  (credentials from @HamyonAPIBot)

    POST {base}/payment/create   form: shop_id, shop_key, amount, order_id
         -> {payment_id, order_id, amount, card, expires_in, expire_at, message}
         -> 400 {"error": "..."}
    GET  {base}/payment/status   ?payment_id=...   (optional read)
    POST {base}/payment/cancel   close a payment early
    POST prepare_url             Hamyon -> us: payment started
    POST complete_url            Hamyon -> us: status=paid | cancel (reason=timeout)

Callback signature:  sign = md5(shop_id + payment_id + amount + shop_key)
Payments expire after 5 minutes. Two OPEN payments with the SAME amount are
not allowed per shop (payments are matched by amount) — the docs recommend
bumping the amount by 1-2 so'm, which ``create_payment`` does automatically.
The complete callback may be delivered more than once (retries 3/10/30/60s):
crediting is idempotent on payment_id.

``shop_key`` is used server-side only and is never logged or returned.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("vyron.hamyon")

REQUEST_TIMEOUT = 20.0
MAX_AMOUNT_BUMPS = 25


class HamyonError(Exception):
    def __init__(self, message: str, code: str = "HAMYON_ERROR", retryable: bool = False):
        super().__init__(message)
        self.safe_message = message
        self.code = code
        self.retryable = retryable


class HamyonNotConfigured(HamyonError):
    def __init__(self) -> None:
        super().__init__("Hamyon API NOT CONFIGURED", "NOT_CONFIGURED")


@dataclass
class HamyonPayment:
    payment_id: str
    amount: int
    card: Optional[str]
    expires_in: Optional[int]
    expire_at: Optional[int]
    raw: dict = field(default_factory=dict)


def _is_duplicate_amount_error(message: str) -> bool:
    text = (message or "").lower()
    return ("summa" in text and "ochiq" in text) or "open payment" in text or "mavjud" in text


def compute_sign(shop_id: str, payment_id: str, amount: Any, shop_key: str) -> str:
    raw = f"{shop_id}{payment_id}{amount}{shop_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _amount_variants(amount: Any) -> list[str]:
    """String forms Hamyon may have used for amount when signing."""
    variants: list[str] = []
    text = str(amount).strip()
    if text:
        variants.append(text)
    try:
        as_float = float(text)
        as_int = int(as_float)
        for v in (str(as_int), f"{as_float}", f"{as_float:.2f}"):
            if v not in variants:
                variants.append(v)
    except (TypeError, ValueError):
        pass
    return variants


class HamyonClient:
    code = "hamyon"

    @property
    def shop_id(self) -> str:
        return get_settings().hamyon_shop_id

    @property
    def _shop_key(self) -> str:
        return get_settings().hamyon_shop_key

    @property
    def base_url(self) -> str:
        return get_settings().hamyon_base_url.rstrip("/")

    def configured(self) -> bool:
        return get_settings().hamyon_configured()

    # ------------------------------------------------------------------
    async def _call(self, method: str, path: str, data: dict) -> dict:
        if not self.configured():
            raise HamyonNotConfigured()
        payload = {"shop_id": self.shop_id, "shop_key": self._shop_key, **data}
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                if method == "GET":
                    response = await client.get(url, params=payload)
                else:
                    response = await client.post(url, data=payload)
        except httpx.TimeoutException as exc:
            raise HamyonError("Payment service timed out", "TIMEOUT", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise HamyonError("Payment service connection error", "CONNECTION", retryable=True) from exc

        try:
            body = response.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {"data": body}
        if response.status_code >= 500:
            raise HamyonError(f"Payment service HTTP {response.status_code}", "HTTP_5XX", retryable=True)
        if response.status_code >= 400 or body.get("error"):
            message = str(body.get("error") or body.get("message") or f"HTTP {response.status_code}")
            raise HamyonError(message[:200], "REJECTED")
        return body

    async def create_payment(self, amount: int, order_id: str) -> HamyonPayment:
        """Create a payment; bumps amount by +1 so'm when an equal open payment exists."""
        current = int(amount)
        last_error: Optional[HamyonError] = None
        for _ in range(MAX_AMOUNT_BUMPS):
            try:
                body = await self._call(
                    "POST", "/payment/create", {"amount": current, "order_id": order_id}
                )
            except HamyonError as exc:
                if exc.code == "REJECTED" and _is_duplicate_amount_error(exc.safe_message):
                    last_error = exc
                    current += 1
                    continue
                raise
            payment_id = body.get("payment_id")
            if not payment_id:
                raise HamyonError("Payment service returned no payment_id", "BAD_RESPONSE")
            try:
                paid_amount = int(float(body.get("amount", current)))
            except (TypeError, ValueError):
                paid_amount = current
            return HamyonPayment(
                payment_id=str(payment_id),
                amount=paid_amount,
                card=str(body.get("card")) if body.get("card") else None,
                expires_in=_to_int(body.get("expires_in")),
                expire_at=_to_int(body.get("expire_at")),
                raw={k: v for k, v in body.items() if "key" not in k.lower()},
            )
        raise last_error or HamyonError("Could not allocate a unique amount", "AMOUNT_BUSY")

    async def get_status(self, payment_id: str) -> dict:
        body = await self._call("GET", "/payment/status", {"payment_id": payment_id})
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return {k: v for k, v in data.items() if "key" not in str(k).lower()}

    async def cancel(self, payment_id: str) -> dict:
        return await self._call("POST", "/payment/cancel", {"payment_id": payment_id})

    # ------------------------------------------------------------------
    def verify_sign(self, payment_id: Any, amount: Any, sign: Any) -> bool:
        """Constant-time verification of md5(shop_id + payment_id + amount + shop_key)."""
        if not self.configured() or not payment_id or not sign:
            return False
        received = str(sign).strip().lower()
        for amount_text in _amount_variants(amount):
            expected = compute_sign(self.shop_id, str(payment_id), amount_text, self._shop_key)
            if hmac.compare_digest(expected, received):
                return True
        return False


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_status(raw: Any) -> str:
    """Map Hamyon statuses onto internal ones. Unknown never means PAID."""
    text = str(raw or "").strip().lower()
    if text in ("paid", "success", "completed"):
        return "PAID"
    if text in ("cancel", "canceled", "cancelled", "expired", "timeout"):
        return "CANCELLED"
    if text in ("pending", "prepare", "created", "waiting"):
        return "PENDING"
    return "UNKNOWN"


_client: HamyonClient | None = None


def get_hamyon() -> HamyonClient:
    global _client
    if _client is None:
        _client = HamyonClient()
    return _client

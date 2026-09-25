"""Payerpin reseller API adapter (v2).

Official endpoints (per Payerpin reseller documentation):
    GET  /api/v2/me
    GET  /api/v2/balance
    GET  /api/v2/catalog
    GET  /api/v2/catalog/{gameKey}
    POST /api/v2/order
    GET  /api/v2/order/{id}
    GET  /api/v2/orders

Auth: server-to-server header  X-API-Key: ${PAYERPIN_API_KEY}
If the configured account documentation differs, follow that documentation.

NEVER logs or stores the API key. All responses are sanitized before storage.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.logging_config import get_logger
from app.providers.suppliers.base import (
    InsufficientBalance,
    SupplierBalanceResult,
    SupplierError,
    SupplierNotConfigured,
    SupplierOrderResult,
    SupplierProvider,
    SupplierStatusResult,
    SupplierTemporaryError,
)

log = get_logger("vyron.payerpin")

REQUEST_TIMEOUT = 25.0

# Internal status layer. Unknown supplier statuses NEVER map to COMPLETED.
_STATUS_MAP = {
    "pending": "PENDING",
    "processing": "PROCESSING",
    "in_progress": "PROCESSING",
    "inprogress": "PROCESSING",
    "success": "COMPLETED",
    "completed": "COMPLETED",
    "done": "COMPLETED",
    "complete": "COMPLETED",
    "failed": "FAILED",
    "error": "FAILED",
    "cancelled": "FAILED",
    "canceled": "FAILED",
    "refunded": "FAILED",
    "insufficient_balance": "FAILED",
}


def map_supplier_status(raw_status: Any) -> str:
    if raw_status is None:
        return "UNKNOWN"
    key = str(raw_status).strip().lower()
    return _STATUS_MAP.get(key, "UNKNOWN")


def _sanitize(obj: Any) -> Any:
    """Strip anything that could be a credential before persisting."""
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if any(s in k.lower() for s in ("key", "secret", "token", "password")) else _sanitize(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj[:50]]
    return obj


class PayerpinProvider(SupplierProvider):
    code = "payerpin"

    # Credentials are read from live settings on every call so that an admin
    # updating the key in the panel takes effect without a restart.
    @property
    def _api_key(self) -> str:
        return get_settings().payerpin_api_key

    @property
    def _base_url(self) -> str:
        return get_settings().payerpin_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        if not self.configured:
            raise SupplierNotConfigured()
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=json_body, params=params
                )
        except httpx.TimeoutException as exc:
            raise SupplierTemporaryError("Supplier request timed out") from exc
        except httpx.HTTPError as exc:
            raise SupplierTemporaryError("Supplier connection error") from exc

        if response.status_code == 401:
            raise SupplierError("Supplier authentication failed", "AUTH_FAILED", retryable=False)
        if response.status_code == 402:
            raise InsufficientBalance()
        if response.status_code == 429 or response.status_code >= 500:
            raise SupplierTemporaryError(f"Supplier HTTP {response.status_code}")
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json())[:300]
            except Exception:
                detail = response.text[:300]
            raise SupplierError(
                f"Supplier rejected request (HTTP {response.status_code})",
                "REJECTED",
                retryable=False,
            ) from None

        try:
            return response.json()
        except Exception as exc:
            raise SupplierError("Invalid supplier response", "BAD_RESPONSE", retryable=True) from exc

    # ---- SupplierProvider interface ----------------------------------------
    async def verify_connection(self) -> dict:
        """GET /api/v2/me — safe. MUST NOT create a top-up."""
        data = await self._request("GET", "/api/v2/me")
        me = data.get("data") if isinstance(data, dict) and "data" in data else data
        return {
            "ok": True,
            "provider": self.code,
            "account": {
                "id": me.get("id") if isinstance(me, dict) else None,
                "name": me.get("name") or me.get("username") if isinstance(me, dict) else None,
            },
        }

    async def get_balance(self) -> SupplierBalanceResult:
        data = await self._request("GET", "/api/v2/balance")
        payload = data.get("data") if isinstance(data, dict) and "data" in data else data
        balance = None
        currency = None
        if isinstance(payload, dict):
            raw_balance = payload.get("balance", payload.get("amount"))
            if raw_balance is not None:
                try:
                    balance = int(float(raw_balance) * 100)  # store minor units
                except (TypeError, ValueError):
                    balance = None
            currency = payload.get("currency")
        return SupplierBalanceResult(balance=balance, currency=currency, raw=_sanitize(data))

    async def get_catalog(self) -> dict:
        return await self._request("GET", "/api/v2/catalog")

    async def get_game_catalog(self, game_key: str) -> dict:
        return await self._request("GET", f"/api/v2/catalog/{game_key}")

    async def create_order(
        self,
        *,
        supplier_product_id: str,
        variation_id: Optional[str],
        player_info: dict,
        idempotency_key: str,
        reference: str,
    ) -> SupplierOrderResult:
        body: dict[str, Any] = {
            "productId": supplier_product_id,
            "reference": reference,
            "idempotencyKey": idempotency_key,
        }
        if variation_id:
            body["variationId"] = variation_id
        body.update({k: v for k, v in player_info.items() if v not in (None, "")})
        data = await self._request("POST", "/api/v2/order", json_body=body)
        payload = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(payload, dict):
            payload = {}
        supplier_order_id = (
            payload.get("id") or payload.get("orderId") or payload.get("order_id")
        )
        raw_status = payload.get("status")
        status = map_supplier_status(raw_status)
        return SupplierOrderResult(
            supplier_order_id=str(supplier_order_id) if supplier_order_id is not None else None,
            status=status,
            raw=_sanitize(data),
            error_code=None if status != "FAILED" else "SUPPLIER_FAILED",
            error_message=None if status != "FAILED" else "Supplier reported failure",
        )

    async def get_order_status(self, supplier_order_id: str) -> SupplierStatusResult:
        data = await self._request("GET", f"/api/v2/order/{supplier_order_id}")
        payload = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(payload, dict):
            payload = {}
        return SupplierStatusResult(
            supplier_order_id=str(
                payload.get("id") or payload.get("orderId") or supplier_order_id
            ),
            status=map_supplier_status(payload.get("status")),
            raw=_sanitize(data),
        )


_provider: PayerpinProvider | None = None


def get_payerpin() -> PayerpinProvider:
    global _provider
    if _provider is None:
        _provider = PayerpinProvider()
    return _provider

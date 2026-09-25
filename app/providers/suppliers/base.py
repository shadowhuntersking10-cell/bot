"""Supplier provider interface (used by Payerpin and future suppliers)."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


class SupplierError(Exception):
    """Base supplier error with a safe message (never contains credentials)."""

    def __init__(self, message: str, code: str = "SUPPLIER_ERROR", retryable: bool = False):
        super().__init__(message)
        self.safe_message = message
        self.code = code
        self.retryable = retryable


class SupplierNotConfigured(SupplierError):
    def __init__(self) -> None:
        super().__init__("Supplier NOT CONFIGURED", "NOT_CONFIGURED", retryable=False)


class SupplierTemporaryError(SupplierError):
    def __init__(self, message: str = "Supplier temporarily unavailable"):
        super().__init__(message, "TEMPORARY", retryable=True)


class InsufficientBalance(SupplierError):
    def __init__(self) -> None:
        super().__init__("Insufficient supplier balance", "INSUFFICIENT_BALANCE", retryable=False)


@dataclass
class SupplierOrderResult:
    supplier_order_id: Optional[str]
    status: str
    raw: dict = field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class SupplierStatusResult:
    supplier_order_id: Optional[str]
    status: str
    raw: dict = field(default_factory=dict)


@dataclass
class SupplierBalanceResult:
    balance: Optional[int]
    currency: Optional[str]
    raw: dict = field(default_factory=dict)


class SupplierProvider(abc.ABC):
    code: str = "base"

    @abc.abstractmethod
    async def verify_connection(self) -> dict:
        """Safely verify credentials. MUST NOT create a top-up order."""

    @abc.abstractmethod
    async def get_balance(self) -> SupplierBalanceResult: ...

    @abc.abstractmethod
    async def get_catalog(self) -> dict: ...

    @abc.abstractmethod
    async def get_game_catalog(self, game_key: str) -> dict: ...

    @abc.abstractmethod
    async def create_order(
        self,
        *,
        supplier_product_id: str,
        variation_id: Optional[str],
        player_info: dict,
        idempotency_key: str,
        reference: str,
    ) -> SupplierOrderResult: ...

    @abc.abstractmethod
    async def get_order_status(self, supplier_order_id: str) -> SupplierStatusResult: ...

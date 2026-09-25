"""Automatic fulfillment worker: Payerpin orders with locking + safe retries.

NEVER fulfills before verified payment. Duplicate protection via a unique
supplier transaction per order (idempotency_key) and DB row locking.
"""
from __future__ import annotations

import asyncio
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.logging_config import get_logger
from app.models import (
    Fulfillment,
    FulfillmentStatus,
    Order,
    OrderItem,
    OrderStatus,
    Supplier,
    SupplierTransaction,
)
from app.providers.suppliers.base import SupplierError, SupplierNotConfigured
from app.providers.suppliers.payerpin import get_payerpin
from app.services.notifications import notify_user

log = get_logger("vyron.fulfillment")

WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
LOCK_TIMEOUT_SECONDS = 120


def _auto_refund(session: Session, order: Order) -> None:
    """Definitive failure only (never on timeouts/unknown): wallet refund if enabled."""
    from app.services.wallet import maybe_auto_refund

    maybe_auto_refund(session, order)


class FulfillmentService:
    def __init__(self) -> None:
        self._stop = asyncio.Event()

    # ---- single-order processing (also called by admin retry) ---------------
    def process_order(self, session: Session, order_id: int) -> str:
        """Claim + process one paid order. Returns outcome label."""
        order = session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            return "not_found"
        if order.status not in (OrderStatus.PAID, OrderStatus.FULFILLMENT_PENDING):
            return f"skipped:{order.status.value}"

        fulfillment = session.execute(
            select(Fulfillment).where(Fulfillment.order_id == order.id).with_for_update()
        ).scalar_one_or_none()
        if fulfillment is None:
            fulfillment = Fulfillment(order_id=order.id, status=FulfillmentStatus.PENDING)
            session.add(fulfillment)
            session.flush()

        # lock claim (prevents multiple workers on the same order)
        if (
            fulfillment.locked_by
            and fulfillment.locked_at
            and (datetime.utcnow() - fulfillment.locked_at).seconds < LOCK_TIMEOUT_SECONDS
            and fulfillment.locked_by != WORKER_ID
        ):
            return "locked"
        fulfillment.locked_by = WORKER_ID
        fulfillment.locked_at = datetime.utcnow()

        # duplicate protection: exactly one supplier transaction per order
        existing_tx = session.execute(
            select(SupplierTransaction)
            .where(SupplierTransaction.order_id == order.id)
            .with_for_update()
        ).scalars().first()
        if existing_tx is not None and existing_tx.supplier_order_id:
            return self._poll_existing(session, order, fulfillment, existing_tx)

        if fulfillment.attempts >= fulfillment.max_attempts:
            fulfillment.status = FulfillmentStatus.FAILED
            fulfillment.last_error = "max_attempts_exceeded"
            order.status = OrderStatus.FAILED
            notify_user(
                session, order.user, kind="order", title_key="order_failed",
                body_key="order_failed",
                params={"order_number": order.order_number, "status": order.status.value},
            )
            return "max_attempts"

        # supplier + variant mapping checks
        supplier = session.execute(
            select(Supplier).where(Supplier.code == "payerpin")
        ).scalar_one_or_none()
        if supplier is None or not supplier.active:
            fulfillment.last_error = "supplier_unavailable"
            self._schedule_retry(fulfillment)
            return "supplier_unavailable"

        item: OrderItem = order.items[0]
        variant = item.variant
        supplier_product_id = (item.snapshot or {}).get("supplier_product_id") or (
            variant.supplier_product_id if variant else None
        )
        variation_id = (item.snapshot or {}).get("supplier_variation_id") or (
            variant.supplier_variation_id if variant else None
        )
        if not supplier_product_id:
            fulfillment.status = FulfillmentStatus.FAILED
            fulfillment.last_error = "not_mapped_to_supplier"
            order.status = OrderStatus.FAILED
            log.error("order %s not mapped to supplier product", order.order_number)
            _auto_refund(session, order)
            return "not_mapped"

        if existing_tx is None:
            existing_tx = SupplierTransaction(
                order_id=order.id,
                supplier_id=supplier.id,
                idempotency_key=f"sup:{order.idempotency_key}",
                supplier_product_id=supplier_product_id,
                variation_id=variation_id,
                request_status="PENDING",
                fulfillment_status=FulfillmentStatus.PENDING,
                request_payload={"reference": order.order_number},
            )
            session.add(existing_tx)
            session.flush()

        # get FULL player info from item snapshot
        player_info = (item.snapshot or {}).get("player_info_full") or order.player_info or {}

        order.status = OrderStatus.FULFILLMENT_PENDING
        fulfillment.status = FulfillmentStatus.PROCESSING
        existing_tx.attempts += 1
        fulfillment.attempts += 1

        # NOTE: network call happens outside the DB transaction in the async
        # wrapper; here we mark intent, then the async layer calls the API and
        # commits the result via record_supplier_result().
        session.info["pending_supplier_call"] = {
            "order_id": order.id,
            "tx_id": existing_tx.id,
            "supplier_product_id": supplier_product_id,
            "variation_id": variation_id,
            "player_info": player_info,
            "reference": order.order_number,
            "idempotency_key": existing_tx.idempotency_key,
        }
        return "dispatched"

    def record_supplier_result(
        self,
        session: Session,
        tx_id: int,
        *,
        supplier_order_id: Optional[str],
        status: str,
        raw: dict,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        tx = session.execute(
            select(SupplierTransaction).where(SupplierTransaction.id == tx_id).with_for_update()
        ).scalar_one_or_none()
        if tx is None:
            return
        tx.supplier_order_id = supplier_order_id or tx.supplier_order_id
        tx.response_status = status
        tx.response_payload = raw
        tx.error_code = error_code
        tx.error_message = error_message
        order = tx.order
        fulfillment = order.fulfillments[0] if order.fulfillments else None

        if status == "COMPLETED":
            tx.request_status = "SENT"
            tx.fulfillment_status = FulfillmentStatus.COMPLETED
            order.status = OrderStatus.COMPLETED
            order.completed_at = datetime.utcnow()
            if fulfillment:
                fulfillment.status = FulfillmentStatus.COMPLETED
                fulfillment.locked_by = None
            notify_user(
                session, order.user, kind="order", title_key="order_completed",
                body_key="order_completed",
                params={
                    "order_number": order.order_number,
                    "status": order.status.value,
                    "total": order.total_amount,
                    "currency": order.currency,
                },
            )
        elif status == "PROCESSING":
            tx.request_status = "SENT"
            tx.fulfillment_status = FulfillmentStatus.AWAITING_STATUS
            order.status = OrderStatus.SUPPLIER_PROCESSING
            if fulfillment:
                fulfillment.status = FulfillmentStatus.AWAITING_STATUS
                fulfillment.locked_by = None
                fulfillment.next_attempt_at = datetime.utcnow() + timedelta(seconds=30)
        elif status == "FAILED":
            tx.fulfillment_status = FulfillmentStatus.FAILED
            order.status = OrderStatus.FAILED
            if fulfillment:
                fulfillment.status = FulfillmentStatus.FAILED
                fulfillment.last_error = error_message or "supplier_failed"
                fulfillment.locked_by = None
            notify_user(
                session, order.user, kind="order", title_key="order_failed",
                body_key="order_failed",
                params={"order_number": order.order_number, "status": order.status.value},
            )
            _auto_refund(session, order)
        else:  # UNKNOWN — never complete
            tx.request_status = "SENT"
            tx.fulfillment_status = FulfillmentStatus.AWAITING_STATUS
            order.status = OrderStatus.SUPPLIER_PROCESSING
            if fulfillment:
                fulfillment.status = FulfillmentStatus.AWAITING_STATUS
                fulfillment.locked_by = None
                self._schedule_retry(fulfillment, minutes=2)

    def record_supplier_error(
        self, session: Session, tx_id: int, *, code: str, message: str, retryable: bool
    ) -> None:
        tx = session.execute(
            select(SupplierTransaction).where(SupplierTransaction.id == tx_id).with_for_update()
        ).scalar_one_or_none()
        if tx is None:
            return
        tx.error_code = code
        tx.error_message = message
        tx.request_status = "ERROR"
        order = tx.order
        fulfillment = order.fulfillments[0] if order.fulfillments else None
        if not retryable:
            tx.fulfillment_status = FulfillmentStatus.FAILED
            order.status = OrderStatus.FAILED
            if fulfillment:
                fulfillment.status = FulfillmentStatus.FAILED
                fulfillment.last_error = message
                fulfillment.locked_by = None
            notify_user(
                session, order.user, kind="order", title_key="order_failed",
                body_key="order_failed",
                params={"order_number": order.order_number, "status": order.status.value},
            )
            if not tx.supplier_order_id:  # rejected at creation => nothing delivered
                _auto_refund(session, order)
        else:
            if fulfillment:
                fulfillment.last_error = message
                fulfillment.locked_by = None
                self._schedule_retry(fulfillment)

    def _poll_existing(
        self, session: Session, order: Order, fulfillment: Fulfillment, tx: SupplierTransaction
    ) -> str:
        session.info["pending_supplier_poll"] = {
            "tx_id": tx.id,
            "supplier_order_id": tx.supplier_order_id,
        }
        return "poll"

    @staticmethod
    def _schedule_retry(fulfillment: Fulfillment, minutes: int = 1) -> None:
        delay = min(minutes * (2 ** min(fulfillment.attempts, 6)), 60 * 6)
        fulfillment.status = FulfillmentStatus.PENDING
        fulfillment.next_attempt_at = datetime.utcnow() + timedelta(minutes=delay)
        fulfillment.locked_by = None


# ---- async worker loop ------------------------------------------------------
class FulfillmentWorker:
    def __init__(self) -> None:
        self.service = FulfillmentService()
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        settings = get_settings()
        if not settings.fulfillment_worker_enabled:
            log.info("fulfillment worker disabled")
            return
        log.info("fulfillment worker started id=%s", WORKER_ID)
        while not self._stopped.is_set():
            try:
                await self.tick()
            except Exception as exc:
                log.warning("fulfillment tick error: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=settings.fulfillment_poll_seconds
                )
            except asyncio.TimeoutError:
                pass
        log.info("fulfillment worker stopped")

    async def tick(self) -> None:
        # claim a batch of due orders
        order_ids: list[int] = []
        with session_scope() as session:
            rows = (
                session.execute(
                    select(Fulfillment.order_id)
                    .join(Order, Order.id == Fulfillment.order_id)
                    .where(
                        Fulfillment.status.in_(
                            [FulfillmentStatus.PENDING, FulfillmentStatus.AWAITING_STATUS]
                        ),
                        Order.status.in_(
                            [OrderStatus.PAID, OrderStatus.FULFILLMENT_PENDING, OrderStatus.SUPPLIER_PROCESSING]
                        ),
                        or_(
                            Fulfillment.next_attempt_at.is_(None),
                            Fulfillment.next_attempt_at <= datetime.utcnow(),
                        ),
                    )
                    .order_by(Fulfillment.id)
                    .limit(10)
                )
                .scalars()
                .all()
            )
            order_ids = list(rows)

        for order_id in order_ids:
            await self.process_one(order_id)

    async def process_one(self, order_id: int) -> None:
        call = None
        poll = None
        tx_id = None
        try:
            with session_scope() as session:
                outcome = self.service.process_order(session, order_id)
                call = session.info.get("pending_supplier_call")
                poll = session.info.get("pending_supplier_poll")
                if call:
                    tx_id = call["tx_id"]
                elif poll:
                    tx_id = poll["tx_id"]
            if call:
                await self._do_create(call)
            elif poll:
                await self._do_poll(poll)
        except Exception as exc:
            log.exception("fulfillment order=%s error=%s", order_id, type(exc).__name__)

    async def _do_create(self, call: dict) -> None:
        payerpin = get_payerpin()
        try:
            result = await payerpin.create_order(
                supplier_product_id=call["supplier_product_id"],
                variation_id=call["variation_id"],
                player_info=call["player_info"],
                idempotency_key=call["idempotency_key"],
                reference=call["reference"],
            )
            with session_scope() as session:
                self.service.record_supplier_result(
                    session,
                    call["tx_id"],
                    supplier_order_id=result.supplier_order_id,
                    status=result.status,
                    raw=result.raw,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
        except SupplierError as exc:
            with session_scope() as session:
                self.service.record_supplier_error(
                    session,
                    call["tx_id"],
                    code=exc.code,
                    message=exc.safe_message,
                    retryable=exc.retryable,
                )

    async def _do_poll(self, poll: dict) -> None:
        payerpin = get_payerpin()
        try:
            result = await payerpin.get_order_status(poll["supplier_order_id"])
            with session_scope() as session:
                self.service.record_supplier_result(
                    session,
                    poll["tx_id"],
                    supplier_order_id=result.supplier_order_id,
                    status=result.status,
                    raw=result.raw,
                )
        except SupplierError as exc:
            if not exc.retryable:
                with session_scope() as session:
                    self.service.record_supplier_error(
                        session,
                        poll["tx_id"],
                        code=exc.code,
                        message=exc.safe_message,
                        retryable=False,
                    )


async def run_catalog_scheduler(worker_note: str = "") -> None:
    """Periodic real catalog sync (only when Payerpin is configured)."""
    from app.services.catalog_sync import sync_payerpin_catalog

    settings = get_settings()
    interval = max(5, settings.catalog_sync_interval_minutes) * 60
    while True:
        await asyncio.sleep(interval)
        if get_payerpin().configured:
            try:
                with session_scope() as session:
                    await sync_payerpin_catalog(session)
                log.info("scheduled catalog sync complete")
            except Exception as exc:
                log.warning("scheduled catalog sync failed: %s", type(exc).__name__)

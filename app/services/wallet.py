"""Wallet: balance top-ups (Hamyon API), purchases and refunds.

Invariants
----------
* Balance is stored in minor units (tiyin) on ``users.balance``.
* Every balance change happens under ``SELECT ... FOR UPDATE`` on the user row
  and writes exactly one ``WalletTransaction`` whose ``reference`` is UNIQUE,
  so a replayed callback / double click can never credit or debit twice.
* A top-up is credited ONLY after a signed Hamyon ``complete`` callback
  (md5 signature verified) or a server-side status check against Hamyon —
  never because the browser says so.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import (
    AdminSetting,
    Counter,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    PaymentTransaction,
    TopUp,
    TopUpStatus,
    User,
    WalletTransaction,
    WalletTxKind,
)
from app.providers.payments.hamyon import HamyonError, get_hamyon, normalize_status
from app.services.notifications import notify_user

log = get_logger("vyron.wallet")

TOPUP_TTL_SECONDS = 300  # Hamyon payments expire after 5 minutes

DEFAULT_TOPUP_SETTINGS = {
    "enabled": True,
    "min_amount": 1000,  # so'm
    "max_amount": 10_000_000,  # so'm
    "presets": [10000, 25000, 50000, 100000, 250000, 500000],
    "auto_refund_failed_orders": True,
}


class WalletError(Exception):
    def __init__(self, key: str, detail: str = "", extra: Optional[dict] = None):
        super().__init__(key)
        self.key = key
        self.detail = detail
        self.extra = extra or {}


def topup_settings(session: Session) -> dict:
    row = session.execute(
        select(AdminSetting).where(AdminSetting.key == "topup")
    ).scalar_one_or_none()
    data = dict(DEFAULT_TOPUP_SETTINGS)
    if row and isinstance(row.value, dict):
        data.update(row.value)
    return data


def _next_topup_reference(session: Session) -> str:
    day = datetime.utcnow().strftime("%Y%m%d")
    name = f"topup:{day}"
    counter = session.execute(
        select(Counter).where(Counter.name == name).with_for_update()
    ).scalar_one_or_none()
    if counter is None:
        counter = Counter(name=name, value=0)
        session.add(counter)
        session.flush()
    counter.value += 1
    return f"TOP-{day}-{counter.value:06d}"


# ---------------------------------------------------------------------------
# Ledger primitive
# ---------------------------------------------------------------------------
def apply_balance_change(
    session: Session,
    user_id: int,
    amount: int,
    kind: WalletTxKind,
    reference: str,
    *,
    note: Optional[str] = None,
    admin_user_id: Optional[int] = None,
    allow_negative: bool = False,
) -> Optional[WalletTransaction]:
    """Atomically change a user's balance. Returns None if reference already applied."""
    existing = session.execute(
        select(WalletTransaction).where(WalletTransaction.reference == reference)
    ).scalar_one_or_none()
    if existing is not None:
        return None
    user = session.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one()
    new_balance = int(user.balance or 0) + int(amount)
    if new_balance < 0 and not allow_negative:
        raise WalletError("insufficient_balance", extra={"balance": int(user.balance or 0)})
    user.balance = new_balance
    tx = WalletTransaction(
        user_id=user.id,
        kind=kind,
        amount=int(amount),
        balance_after=new_balance,
        reference=reference,
        note=(note or "")[:255] or None,
        admin_user_id=admin_user_id,
    )
    session.add(tx)
    session.flush()
    return tx


# ---------------------------------------------------------------------------
# Top-ups
# ---------------------------------------------------------------------------
def active_topup(session: Session, user: User) -> Optional[TopUp]:
    now = datetime.utcnow()
    return session.execute(
        select(TopUp)
        .where(
            TopUp.user_id == user.id,
            TopUp.status == TopUpStatus.PENDING,
            TopUp.expires_at > now,
        )
        .order_by(TopUp.id.desc())
    ).scalars().first()


async def create_topup(session: Session, user: User, amount: int) -> TopUp:
    hamyon = get_hamyon()
    if not hamyon.configured():
        raise WalletError("payment_not_configured")
    settings = topup_settings(session)
    if not settings.get("enabled", True):
        raise WalletError("topup_disabled")
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise WalletError("invalid_amount")
    if amount < int(settings["min_amount"]) or amount > int(settings["max_amount"]):
        raise WalletError(
            "topup_amount_range",
            extra={"min": int(settings["min_amount"]), "max": int(settings["max_amount"])},
        )

    existing = active_topup(session, user)
    if existing is not None:
        return existing  # one open card payment per user at a time

    topup = TopUp(
        reference=_next_topup_reference(session),
        user_id=user.id,
        provider="hamyon",
        requested_amount=amount,
        pay_amount=amount,
        status=TopUpStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(seconds=TOPUP_TTL_SECONDS),
    )
    session.add(topup)
    session.flush()
    try:
        created = await hamyon.create_payment(amount, topup.reference)
    except HamyonError as exc:
        topup.status = TopUpStatus.FAILED
        topup.cancel_reason = exc.code[:64]
        session.commit()
        log.warning("hamyon create failed ref=%s code=%s", topup.reference, exc.code)
        raise WalletError("payment_provider_error", exc.safe_message)

    topup.provider_payment_id = created.payment_id
    topup.pay_amount = created.amount
    topup.card_number = created.card
    if created.expire_at:
        topup.expires_at = datetime.utcfromtimestamp(created.expire_at)
    elif created.expires_in:
        topup.expires_at = datetime.utcnow() + timedelta(seconds=created.expires_in)
    session.flush()
    log.info("topup created ref=%s user=%s amount=%s", topup.reference, user.id, topup.pay_amount)
    return topup


def mark_topup_paid(session: Session, topup: TopUp, paid_amount_som: int, *, source: str) -> bool:
    """Credit a top-up exactly once. Returns True if credited now."""
    topup = session.execute(
        select(TopUp).where(TopUp.id == topup.id).with_for_update()
    ).scalar_one()
    if topup.status == TopUpStatus.PAID:
        return False
    if int(paid_amount_som) != int(topup.pay_amount):
        log.warning(
            "topup amount mismatch ref=%s expected=%s got=%s",
            topup.reference, topup.pay_amount, paid_amount_som,
        )
        raise WalletError("amount_mismatch")
    credit_minor = int(topup.pay_amount) * 100
    tx = apply_balance_change(
        session,
        topup.user_id,
        credit_minor,
        WalletTxKind.TOPUP,
        f"topup:{topup.id}",
        note=f"Hamyon {topup.reference} ({source})",
    )
    topup.status = TopUpStatus.PAID
    topup.paid_at = datetime.utcnow()
    topup.credited_amount = credit_minor
    if tx is not None:
        notify_user(
            session,
            topup.user,
            kind="wallet",
            title_key="topup_paid",
            body_key="topup_paid",
            params={"amount": credit_minor, "currency": "UZS", "reference": topup.reference},
        )
    log.info("topup credited ref=%s source=%s", topup.reference, source)
    return tx is not None


def mark_topup_cancelled(session: Session, topup: TopUp, reason: str) -> None:
    topup = session.execute(
        select(TopUp).where(TopUp.id == topup.id).with_for_update()
    ).scalar_one()
    if topup.status != TopUpStatus.PENDING:
        return
    topup.status = TopUpStatus.CANCELLED
    topup.cancel_reason = (reason or "cancel")[:64]


async def refresh_topup_status(session: Session, topup: TopUp) -> TopUp:
    """Server-side status check against Hamyon (fallback when a callback was missed)."""
    if topup.status != TopUpStatus.PENDING or not topup.provider_payment_id:
        return topup
    hamyon = get_hamyon()
    if not hamyon.configured():
        return topup
    try:
        data = await hamyon.get_status(topup.provider_payment_id)
    except HamyonError as exc:
        log.info("hamyon status check failed ref=%s code=%s", topup.reference, exc.code)
        return topup
    topup.last_checked_at = datetime.utcnow()
    status = normalize_status(data.get("status"))
    if status == "PAID":
        raw_amount = data.get("amount", topup.pay_amount)
        try:
            amount = int(float(raw_amount))
        except (TypeError, ValueError):
            amount = -1
        try:
            mark_topup_paid(session, topup, amount, source="status")
        except WalletError:
            pass
    elif status == "CANCELLED":
        mark_topup_cancelled(session, topup, str(data.get("reason") or "cancel"))
    return topup


def expire_stale_topups(session: Session) -> int:
    """Mark long-expired pending top-ups cancelled (grace 10 min for late callbacks)."""
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    rows = session.execute(
        select(TopUp).where(TopUp.status == TopUpStatus.PENDING, TopUp.expires_at < cutoff)
    ).scalars().all()
    for row in rows:
        row.status = TopUpStatus.CANCELLED
        row.cancel_reason = "timeout"
    return len(rows)


def serialize_topup(topup: TopUp, *, admin: bool = False) -> dict:
    data = {
        "id": topup.id,
        "reference": topup.reference,
        "status": topup.status.value if hasattr(topup.status, "value") else topup.status,
        "requested_amount": topup.requested_amount,
        "pay_amount": topup.pay_amount,
        "credited_amount": topup.credited_amount,
        "card_number": topup.card_number if topup.status == TopUpStatus.PENDING or admin else None,
        "expires_at": (topup.expires_at.isoformat() + "Z") if topup.expires_at else None,
        "seconds_left": max(0, int((topup.expires_at - datetime.utcnow()).total_seconds()))
        if topup.expires_at and topup.status == TopUpStatus.PENDING
        else 0,
        "paid_at": topup.paid_at.isoformat() if topup.paid_at else None,
        "created_at": topup.created_at.isoformat() if topup.created_at else None,
        "cancel_reason": topup.cancel_reason,
    }
    if admin:
        data["user_id"] = topup.user_id
        data["username"] = topup.user.username if topup.user else None
        data["provider_payment_id"] = topup.provider_payment_id
    return data


def serialize_wallet_tx(tx: WalletTransaction) -> dict:
    return {
        "id": tx.id,
        "kind": tx.kind.value if hasattr(tx.kind, "value") else tx.kind,
        "amount": tx.amount,
        "balance_after": tx.balance_after,
        "reference": tx.reference,
        "note": tx.note,
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
    }


# ---------------------------------------------------------------------------
# Paying orders with balance / refunds
# ---------------------------------------------------------------------------
def pay_order_with_balance(session: Session, user: User, order: Order) -> Payment:
    """Debit the wallet and mark the order PAID (single DB transaction)."""
    from app.services.orders import mark_order_paid

    order = session.execute(
        select(Order).where(Order.id == order.id).with_for_update()
    ).scalar_one()
    if order.user_id != user.id:
        raise WalletError("order_not_found")
    if order.status != OrderStatus.PENDING_PAYMENT:
        paid = next((p for p in order.payments if p.status == PaymentStatus.PAID), None)
        if paid is not None:
            return paid
        raise WalletError("order_not_payable")

    apply_balance_change(
        session,
        user.id,
        -int(order.total_amount),
        WalletTxKind.PURCHASE,
        f"order:{order.order_number}",
        note=f"Order {order.order_number}",
    )
    payment = Payment(
        order_id=order.id,
        provider="balance",
        provider_payment_id=f"wallet:{order.order_number}",
        idempotency_key=f"balance:{order.idempotency_key}",
        amount=order.total_amount,
        currency=order.currency,
        status=PaymentStatus.PAID,
        paid_at=datetime.utcnow(),
    )
    session.add(payment)
    session.flush()
    session.add(
        PaymentTransaction(
            payment_id=payment.id,
            kind="BALANCE",
            provider_event_id=f"balance:{order.order_number}",
            status="PAID",
            payload={"source": "wallet"},
        )
    )
    mark_order_paid(session, order)
    return payment


def refund_order_to_balance(
    session: Session, order: Order, *, reason: str, admin_user_id: Optional[int] = None
) -> bool:
    """Credit the order total back to the customer's wallet exactly once."""
    paid = [p for p in order.payments if p.status == PaymentStatus.PAID]
    if not paid:
        return False
    amount = sum(p.amount for p in paid)
    tx = apply_balance_change(
        session,
        order.user_id,
        int(amount),
        WalletTxKind.REFUND,
        f"refund:{order.order_number}",
        note=(reason or "refund")[:200],
        admin_user_id=admin_user_id,
    )
    if tx is None:
        return False
    for p in paid:
        p.status = PaymentStatus.REFUNDED
    order.status = OrderStatus.REFUNDED
    notify_user(
        session,
        order.user,
        kind="order",
        title_key="order_refunded",
        body_key="order_refunded",
        params={"order_number": order.order_number, "total": amount, "currency": order.currency,
                "status": "REFUNDED"},
    )
    log.info("order refunded to balance %s", order.order_number)
    return True


def maybe_auto_refund(session: Session, order: Order) -> None:
    """Business rule (admin-configurable): definitive supplier failure -> refund to wallet."""
    try:
        if not topup_settings(session).get("auto_refund_failed_orders", True):
            return
        if order.status != OrderStatus.FAILED:
            return
        refund_order_to_balance(session, order, reason="auto: supplier failed")
    except Exception as exc:  # never break the fulfillment worker
        log.error("auto refund failed order=%s err=%s", order.order_number, type(exc).__name__)


def wallet_stats(session: Session) -> dict:
    total_balance = session.execute(select(func.coalesce(func.sum(User.balance), 0))).scalar() or 0
    paid_topups = session.execute(
        select(func.coalesce(func.sum(TopUp.credited_amount), 0)).where(
            TopUp.status == TopUpStatus.PAID
        )
    ).scalar() or 0
    pending = session.execute(
        select(func.count(TopUp.id)).where(TopUp.status == TopUpStatus.PENDING)
    ).scalar() or 0
    return {
        "total_customer_balance": int(total_balance),
        "total_topups": int(paid_topups),
        "pending_topups": int(pending),
    }


async def run_topup_watcher(stop_event=None) -> None:
    """Background safety net: re-check pending top-ups with Hamyon + expire old ones.

    Hamyon pushes the result to complete_url; this loop only covers missed callbacks.
    """
    import asyncio

    from app.db import session_scope

    while stop_event is None or not stop_event.is_set():
        try:
            if get_hamyon().configured():
                now = datetime.utcnow()
                with session_scope() as session:
                    rows = session.execute(
                        select(TopUp)
                        .where(
                            TopUp.status == TopUpStatus.PENDING,
                            TopUp.provider_payment_id.is_not(None),
                            TopUp.created_at < now - timedelta(seconds=20),
                            TopUp.created_at > now - timedelta(minutes=30),
                        )
                        .order_by(TopUp.id)
                        .limit(20)
                    ).scalars().all()
                    for row in rows:
                        if row.last_checked_at and (now - row.last_checked_at).total_seconds() < 20:
                            continue
                        await refresh_topup_status(session, row)
                        session.commit()
                    expire_stale_topups(session)
        except Exception as exc:
            log.warning("topup watcher error: %s", type(exc).__name__)
        await asyncio.sleep(15)

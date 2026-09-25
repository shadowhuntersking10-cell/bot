"""Wallet API: balance, Hamyon card top-ups, ledger history."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import pagination, require_user
from app.db import get_db
from app.models import TopUp, TopUpStatus, User, WalletTransaction
from app.providers.payments.hamyon import HamyonError, get_hamyon
from app.security import rate_limit
from app.services.wallet import (
    WalletError,
    active_topup,
    create_topup,
    mark_topup_cancelled,
    refresh_topup_status,
    serialize_topup,
    serialize_wallet_tx,
    topup_settings,
)

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


class TopUpBody(BaseModel):
    amount: int = Field(ge=1, le=1_000_000_000)


def _wallet_error(exc: WalletError) -> HTTPException:
    status = 400
    if exc.key == "payment_not_configured":
        status = 503
    elif exc.key == "payment_provider_error":
        status = 502
    detail = exc.key
    if exc.extra:
        return HTTPException(status_code=status, detail={"code": exc.key, **exc.extra})
    return HTTPException(status_code=status, detail=detail)


@router.get("")
def wallet(db: Session = Depends(get_db), user: User = Depends(require_user)):
    settings = topup_settings(db)
    topup = active_topup(db, user)
    return {
        "balance": int(user.balance or 0),
        "currency": "UZS",
        "topup_enabled": bool(settings.get("enabled", True)) and get_hamyon().configured(),
        "payment_configured": get_hamyon().configured(),
        "min_amount": int(settings["min_amount"]),
        "max_amount": int(settings["max_amount"]),
        "presets": [int(x) for x in settings.get("presets", [])][:8],
        "active_topup": serialize_topup(topup) if topup else None,
    }


@router.post("/topups")
async def new_topup(
    body: TopUpBody, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    if not rate_limit(f"topup:{user.id}", 6, 60):
        raise HTTPException(status_code=429, detail="rate_limited")
    try:
        topup = await create_topup(db, user, body.amount)
    except WalletError as exc:
        db.rollback()
        raise _wallet_error(exc)
    db.commit()
    return {"topup": serialize_topup(topup)}


@router.get("/topups")
def my_topups(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    page, page_size = pagination(page, page_size)
    query = select(TopUp).where(TopUp.user_id == user.id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    rows = db.execute(
        query.order_by(TopUp.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"items": [serialize_topup(t) for t in rows], "total": total, "page": page,
            "page_size": page_size}


@router.get("/topups/{reference}")
async def topup_status(
    reference: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    topup = db.execute(
        select(TopUp).where(TopUp.reference == reference, TopUp.user_id == user.id)
    ).scalar_one_or_none()
    if topup is None:
        raise HTTPException(status_code=404, detail="not_found")
    # server-side check with Hamyon at most every 10s (callback is primary)
    if topup.status == TopUpStatus.PENDING and (
        topup.last_checked_at is None
        or (datetime.utcnow() - topup.last_checked_at).total_seconds() > 10
    ) and (datetime.utcnow() - topup.created_at).total_seconds() > 10:
        await refresh_topup_status(db, topup)
        db.commit()
    db.refresh(user)
    return {"topup": serialize_topup(topup), "balance": int(user.balance or 0)}


@router.post("/topups/{reference}/cancel")
async def cancel_topup(
    reference: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    topup = db.execute(
        select(TopUp).where(TopUp.reference == reference, TopUp.user_id == user.id)
    ).scalar_one_or_none()
    if topup is None:
        raise HTTPException(status_code=404, detail="not_found")
    if topup.status != TopUpStatus.PENDING:
        return {"topup": serialize_topup(topup)}
    # Check first: the money may already have arrived.
    await refresh_topup_status(db, topup)
    if topup.status == TopUpStatus.PENDING and topup.provider_payment_id:
        try:
            await get_hamyon().cancel(topup.provider_payment_id)
        except HamyonError:
            pass  # Hamyon auto-cancels after 5 minutes anyway
        mark_topup_cancelled(db, topup, "user_cancel")
    db.commit()
    return {"topup": serialize_topup(topup)}


@router.get("/transactions")
def transactions(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    page, page_size = pagination(page, page_size)
    query = select(WalletTransaction).where(WalletTransaction.user_id == user.id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    rows = db.execute(
        query.order_by(WalletTransaction.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"items": [serialize_wallet_tx(t) for t in rows], "total": total, "page": page,
            "page_size": page_size}

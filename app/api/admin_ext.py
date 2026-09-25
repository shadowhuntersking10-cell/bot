"""Admin API extensions: integrations (write-only secrets), branding, media,
wallet / Hamyon top-ups, balance adjustments, refunds to balance.

Every route requires ``require_admin`` (server-side RBAC) and writes audit logs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import pagination, require_admin
from app.config import get_settings
from app.db import get_db
from app.logging_config import get_logger
from app.models import (
    Order,
    OrderStatus,
    TopUp,
    TopUpStatus,
    User,
    WalletTransaction,
    WalletTxKind,
)
from app.providers.payments.hamyon import HamyonError, get_hamyon
from app.providers.suppliers.base import SupplierError
from app.providers.suppliers.payerpin import get_payerpin
from app.services import env_store, media
from app.services.audit import write_audit
from app.services.site_settings import (
    DEFAULT_BRANDING,
    get_branding,
    get_rates,
    put_setting,
    sanitize_branding,
    sanitize_rates,
    sanitize_topup,
)
from app.services.wallet import (
    WalletError,
    apply_balance_change,
    refresh_topup_status,
    refund_order_to_balance,
    serialize_topup,
    serialize_wallet_tx,
    topup_settings,
    wallet_stats,
)

log = get_logger("vyron.admin_ext")

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Integrations (credentials -> .env only, never returned)
# ---------------------------------------------------------------------------
class IntegrationsBody(BaseModel):
    values: dict[str, str]


def _callback_urls() -> dict:
    base = get_settings().public_base_url or ""
    return {
        "hamyon_prepare_url": f"{base}/api/webhooks/hamyon/prepare" if base else None,
        "hamyon_complete_url": f"{base}/api/webhooks/hamyon/complete" if base else None,
        "payerpin_webhook_url": f"{base}/api/webhooks/payerpin" if base else None,
        "payment_webhook_url": f"{base}/api/webhooks/payments" if base else None,
        "webapp_url": get_settings().webapp_url or None,
    }


@router.get("/integrations")
def get_integrations(admin: User = Depends(require_admin)):
    from bot.bot import bot_manager

    settings = get_settings()
    return {
        "items": env_store.describe(),
        "callbacks": _callback_urls(),
        "status": {
            "hamyon": settings.hamyon_configured(),
            "payerpin": settings.payerpin_configured(),
            "telegram": settings.telegram_configured(),
            "telegram_bot_running": bot_manager.running,
            "provider_payment": settings.provider_payment_configured(),
            "public_base_url_https": settings.public_base_url.startswith("https://"),
        },
    }


@router.put("/integrations")
async def put_integrations(
    body: IntegrationsBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    from bot.bot import bot_manager

    # Empty string for a secret means "leave unchanged" unless explicitly cleared.
    values = {}
    for key, value in body.values.items():
        if key.endswith("__clear"):
            values[key[: -len("__clear")]] = ""
            continue
        meta = env_store.MANAGED_KEYS.get(key)
        if meta is None:
            raise HTTPException(status_code=400, detail=f"unknown_key:{key}")
        if meta[1] and (value or "").strip() == "":
            continue
        values[key] = value
    if not values:
        return {"ok": True, "changed": []}
    before_token = get_settings().telegram_bot_token
    before_url = get_settings().webapp_url
    try:
        changed = env_store.update(values)
    except env_store.EnvStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit(db, admin_user_id=admin.id, action="integrations.updated",
                target_type="env", target_id=",".join(changed)[:64],
                details={"keys": changed})  # key names only — never values
    db.commit()
    settings = get_settings()
    if settings.telegram_bot_token != before_token or settings.webapp_url != before_url:
        await bot_manager.restart()
    return {"ok": True, "changed": changed}


@router.post("/integrations/test/{name}")
async def test_integration(name: str, db: Session = Depends(get_db),
                           admin: User = Depends(require_admin)):
    """Safe connectivity checks — never creates payments or top-up orders."""
    result: dict = {"ok": False, "name": name}
    settings = get_settings()
    if name == "payerpin":
        if not settings.payerpin_configured():
            result["message"] = "NOT CONFIGURED"
        else:
            try:
                info = await get_payerpin().verify_connection()
                result.update(ok=True, message="Connection successful", account=info.get("account"))
            except SupplierError as exc:
                result["message"] = f"Connection failed: {exc.safe_message}"
    elif name == "hamyon":
        if not settings.hamyon_configured():
            result["message"] = "NOT CONFIGURED"
        else:
            # read-only probe: status of a non-existent payment. A shop-auth error
            # means bad credentials; a "not found" style answer means reachable.
            try:
                await get_hamyon().get_status("vyron-connection-test")
                result.update(ok=True, message="Connection successful")
            except HamyonError as exc:
                text = exc.safe_message.lower()
                if exc.code == "REJECTED" and ("do'kon" in text or "shop" in text or "kalit" in text):
                    result["message"] = f"Credentials rejected: {exc.safe_message}"
                elif exc.code == "REJECTED":
                    result.update(ok=True, message=f"Reachable ({exc.safe_message})")
                else:
                    result["message"] = f"Connection failed: {exc.safe_message}"
    elif name == "telegram":
        if not settings.telegram_configured():
            result["message"] = "NOT CONFIGURED"
        else:
            import httpx

            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    res = await client.get(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe"
                    )
                data = res.json()
                if data.get("ok"):
                    bot = data.get("result", {})
                    result.update(ok=True, message="Connection successful",
                                  bot_username=bot.get("username"))
                else:
                    result["message"] = "Token rejected by Telegram"
            except Exception as exc:
                result["message"] = f"Connection failed: {type(exc).__name__}"
    else:
        raise HTTPException(status_code=404, detail="unknown_integration")
    write_audit(db, admin_user_id=admin.id, action=f"integration.test.{name}",
                target_type="integration", target_id=name,
                result="SUCCESS" if result["ok"] else "FAILED")
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Branding / site settings
# ---------------------------------------------------------------------------
class AnyBody(BaseModel):
    value: dict


@router.get("/branding")
def branding(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return {"branding": get_branding(db), "defaults": DEFAULT_BRANDING}


@router.put("/branding")
def put_branding(body: AnyBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        clean = sanitize_branding(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    value = put_setting(db, "branding", clean)
    write_audit(db, admin_user_id=admin.id, action="branding.updated", target_type="settings",
                target_id="branding", details={"keys": sorted(clean.keys())})
    db.commit()
    return {"branding": value}


@router.get("/topup-settings")
def get_topup_settings(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return {"topup": topup_settings(db), "rates": get_rates(db)}


@router.put("/topup-settings")
def put_topup_settings(body: AnyBody, db: Session = Depends(get_db),
                       admin: User = Depends(require_admin)):
    try:
        clean = sanitize_topup(body.value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    value = put_setting(db, "topup", clean)
    write_audit(db, admin_user_id=admin.id, action="topup_settings.updated",
                target_type="settings", target_id="topup", details=clean)
    db.commit()
    return {"topup": value}


@router.put("/rates")
def put_rates(body: AnyBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        clean = sanitize_rates(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from app.models import AdminSetting

    row = db.execute(select(AdminSetting).where(AdminSetting.key == "rates")).scalar_one_or_none()
    if row is None:
        db.add(AdminSetting(key="rates", value=clean))
    else:
        row.value = clean
    write_audit(db, admin_user_id=admin.id, action="rates.updated", target_type="settings",
                target_id="rates", details=clean)
    db.commit()
    return {"rates": clean}


# ---------------------------------------------------------------------------
# Media uploads
# ---------------------------------------------------------------------------
@router.post("/uploads")
async def upload(
    file: UploadFile = File(...),
    kind: str = Form(default="media"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    data = await file.read(media.MAX_BYTES + 1)
    try:
        saved = media.save_image(data, kind=kind)
    except media.MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit(db, admin_user_id=admin.id, action="media.uploaded", target_type="media",
                target_id=saved["name"][:64])
    db.commit()
    return saved


@router.get("/media")
def list_media(admin: User = Depends(require_admin)):
    return {"items": media.list_media()}


@router.delete("/media/{name}")
def delete_media(name: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        ok = media.delete_media(name)
    except media.MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit(db, admin_user_id=admin.id, action="media.deleted", target_type="media",
                target_id=name[:64])
    db.commit()
    return {"ok": ok}


# ---------------------------------------------------------------------------
# Wallet / top-ups
# ---------------------------------------------------------------------------
@router.get("/topups")
def admin_topups(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(TopUp).join(User, User.id == TopUp.user_id)
    if status:
        try:
            query = query.where(TopUp.status == TopUpStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_status")
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(TopUp.reference.ilike(like), User.username.ilike(like),
                                TopUp.provider_payment_id.ilike(like)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = db.execute(query.order_by(TopUp.id.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).scalars().all()
    return {"items": [serialize_topup(t, admin=True) for t in rows], "total": total,
            "page": page, "page_size": page_size, "stats": wallet_stats(db)}


@router.post("/topups/{topup_id}/recheck")
async def recheck_topup(topup_id: int, db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    topup = db.get(TopUp, topup_id)
    if topup is None:
        raise HTTPException(status_code=404, detail="not_found")
    await refresh_topup_status(db, topup)
    write_audit(db, admin_user_id=admin.id, action="topup.recheck", target_type="topup",
                target_id=topup.reference, details={"status": topup.status.value})
    db.commit()
    return {"topup": serialize_topup(topup, admin=True)}


class BalanceBody(BaseModel):
    amount: int = Field(description="so'm, signed (+ credit / - debit)")
    note: str = Field(min_length=3, max_length=200)


@router.post("/users/{user_id}/balance")
def adjust_balance(user_id: int, body: BalanceBody, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)):
    if body.amount == 0 or abs(body.amount) > 1_000_000_000:
        raise HTTPException(status_code=400, detail="invalid_amount")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    import secrets as _secrets

    try:
        tx = apply_balance_change(
            db, user.id, body.amount * 100, WalletTxKind.ADJUSTMENT,
            f"adjust:{user.id}:{_secrets.token_hex(8)}", note=body.note, admin_user_id=admin.id,
        )
    except WalletError as exc:
        raise HTTPException(status_code=400, detail=exc.key)
    write_audit(db, admin_user_id=admin.id, action="wallet.adjusted", target_type="user",
                target_id=str(user.id), details={"amount": body.amount, "note": body.note})
    db.commit()
    return {"ok": True, "balance": tx.balance_after if tx else user.balance}


@router.get("/wallet/transactions")
def wallet_transactions(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    user_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(WalletTransaction)
    if user_id:
        query = query.where(WalletTransaction.user_id == user_id)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = db.execute(query.order_by(WalletTransaction.id.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).scalars().all()
    items = []
    for tx in rows:
        data = serialize_wallet_tx(tx)
        data["user_id"] = tx.user_id
        items.append(data)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


class RefundBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=200)


@router.post("/orders/{order_id}/refund-balance")
def refund_to_balance(order_id: int, body: RefundBody, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    """Refund a paid order to the customer's wallet (separate from fulfillment)."""
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if order.status in (OrderStatus.PENDING_PAYMENT, OrderStatus.REFUNDED, OrderStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="cannot_refund")
    if order.status == OrderStatus.COMPLETED and not (body.reason or "").strip():
        raise HTTPException(status_code=400, detail="reason_required")
    ok = refund_order_to_balance(db, order, reason=body.reason or "admin refund",
                                 admin_user_id=admin.id)
    if not ok:
        raise HTTPException(status_code=400, detail="cannot_refund")
    write_audit(db, admin_user_id=admin.id, action="order.refunded_to_balance",
                target_type="order", target_id=order.order_number,
                details={"reason": body.reason})
    db.commit()
    return {"ok": True}

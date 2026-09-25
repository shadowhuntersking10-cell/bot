"""Moderated account marketplace.

Sellers submit listings -> admin approves/rejects -> approved listings are
public. Deals are handled manually through VYRON support (never auto-delivered,
no credentials are ever stored here).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import pagination, require_admin, require_user
from app.db import get_db
from app.models import AccountListing, Game, ListingStatus, User
from app.security import rate_limit
from app.services.audit import write_audit

router = APIRouter(tags=["market"])

MAX_OPEN_LISTINGS = 5


class ListingBody(BaseModel):
    game_slug: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(default="", max_length=2000)
    price: int = Field(ge=1000, le=1_000_000_000)  # so'm


class ModerateBody(BaseModel):
    action: Literal["approve", "reject", "sold"]
    note: Optional[str] = Field(default=None, max_length=500)


def _mask(name: Optional[str]) -> str:
    if not name:
        return "user"
    return name[:2] + "***" if len(name) > 2 else name + "***"


def serialize_listing(listing: AccountListing, game: Optional[Game], *, owner: bool = False,
                      admin: bool = False, seller: Optional[User] = None) -> dict:
    status = listing.status.value if hasattr(listing.status, "value") else listing.status
    data = {
        "id": listing.id,
        "title": listing.title,
        "description": listing.description or "",
        "price": listing.price_amount,
        "currency": listing.currency,
        "status": status,
        "game": {"slug": game.slug, "name": game.name, "icon_url": game.icon_url} if game else None,
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "seller": _mask(seller.username if seller else None),
    }
    if owner or admin:
        data["moderation_note"] = listing.moderation_note
    if admin and seller is not None:
        data["seller"] = seller.username
        data["seller_id"] = seller.id
        data["seller_telegram_id"] = seller.telegram_id
    return data


@router.get("/api/market")
def market(
    db: Session = Depends(get_db),
    game: Optional[str] = Query(default=None, max_length=96),
    q: Optional[str] = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=60),
):
    query = select(AccountListing, Game, User).join(Game, Game.id == AccountListing.game_id).join(
        User, User.id == AccountListing.seller_user_id
    ).where(AccountListing.status == ListingStatus.ACTIVE)
    if game:
        query = query.where(Game.slug == game)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(AccountListing.title.ilike(like), Game.name.ilike(like)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = db.execute(
        query.order_by(AccountListing.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [serialize_listing(l, g, seller=u) for l, g, u in rows], "total": total,
            "page": page, "page_size": page_size}


@router.get("/api/market/mine")
def my_listings(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.execute(
        select(AccountListing, Game).join(Game, Game.id == AccountListing.game_id)
        .where(AccountListing.seller_user_id == user.id).order_by(AccountListing.id.desc()).limit(50)
    ).all()
    return {"items": [serialize_listing(l, g, owner=True, seller=user) for l, g in rows]}


@router.post("/api/market")
def create_listing(body: ListingBody, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    if not rate_limit(f"market:{user.id}", 5, 3600):
        raise HTTPException(status_code=429, detail="rate_limited")
    game = db.execute(select(Game).where(Game.slug == body.game_slug, Game.active == True)).scalar_one_or_none()  # noqa: E712
    if game is None:
        raise HTTPException(status_code=400, detail="game_not_found")
    open_count = db.execute(
        select(func.count(AccountListing.id)).where(
            AccountListing.seller_user_id == user.id,
            AccountListing.status.in_([ListingStatus.PENDING_APPROVAL, ListingStatus.ACTIVE]),
        )
    ).scalar() or 0
    if open_count >= MAX_OPEN_LISTINGS:
        raise HTTPException(status_code=400, detail="too_many_listings")
    listing = AccountListing(
        game_id=game.id,
        seller_user_id=user.id,
        title=body.title.strip(),
        description=body.description.strip(),
        price_amount=int(body.price) * 100,
        currency="UZS",
        status=ListingStatus.PENDING_APPROVAL,
    )
    db.add(listing)
    db.commit()
    return {"listing": serialize_listing(listing, game, owner=True, seller=user)}


@router.delete("/api/market/{listing_id}")
def delete_listing(listing_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    listing = db.get(AccountListing, listing_id)
    if listing is None or listing.seller_user_id != user.id:
        raise HTTPException(status_code=404, detail="not_found")
    if listing.status == ListingStatus.SOLD:
        raise HTTPException(status_code=400, detail="invalid_status")
    db.delete(listing)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin moderation
# ---------------------------------------------------------------------------
@router.get("/api/admin/market")
def admin_market(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(AccountListing, Game, User).join(Game, Game.id == AccountListing.game_id).join(
        User, User.id == AccountListing.seller_user_id
    )
    if status:
        try:
            query = query.where(AccountListing.status == ListingStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_status")
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
    page, page_size = pagination(page, page_size)
    rows = db.execute(
        query.order_by(AccountListing.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [serialize_listing(l, g, admin=True, seller=u) for l, g, u in rows],
            "total": total, "page": page, "page_size": page_size}


@router.post("/api/admin/market/{listing_id}/moderate")
def moderate(listing_id: int, body: ModerateBody, db: Session = Depends(get_db),
             admin: User = Depends(require_admin)):
    listing = db.get(AccountListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="not_found")
    listing.status = {
        "approve": ListingStatus.ACTIVE,
        "reject": ListingStatus.REJECTED,
        "sold": ListingStatus.SOLD,
    }[body.action]
    listing.moderation_note = (body.note or "").strip() or None
    write_audit(db, admin_user_id=admin.id, action=f"market.{body.action}",
                target_type="listing", target_id=listing.id)
    seller = db.get(User, listing.seller_user_id)
    if seller is not None and body.action in ("approve", "reject"):
        from app.services.notifications import notify_user

        notify_user(db, seller, kind="market", title_key="admin_message", body_key="custom",
                    params={"title": "VYRON Market",
                            "text": f"{listing.title}: {listing.status.value}"
                                    + (f" — {listing.moderation_note}" if listing.moderation_note else "")})
    db.commit()
    return {"ok": True, "status": listing.status.value}

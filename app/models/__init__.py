"""VYRON SQLAlchemy models (MySQL-compatible).

Money is stored as integer minor units (*_amount). Internal supplier costs are
NEVER exposed through customer-facing APIs.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class RoleName(str, enum.Enum):
    ADMIN = "ADMIN"
    SUPPORT = "SUPPORT"
    CUSTOMER = "CUSTOMER"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    FULFILLMENT_PENDING = "FULFILLMENT_PENDING"
    SUPPLIER_PROCESSING = "SUPPLIER_PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class FulfillmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    AWAITING_STATUS = "AWAITING_STATUS"


class ProductType(str, enum.Enum):
    TOPUP = "TOPUP"
    GIFT_CARD = "GIFT_CARD"
    DIGITAL = "DIGITAL"
    ACCOUNT = "ACCOUNT"


class FulfillmentType(str, enum.Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"
    ACCOUNT = "ACCOUNT"


class CouponType(str, enum.Enum):
    PERCENT = "PERCENT"
    FIXED = "FIXED"


class TopUpStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class WalletTxKind(str, enum.Enum):
    TOPUP = "TOPUP"
    PURCHASE = "PURCHASE"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"


class ListingStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    SOLD = "SOLD"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Auth / users
# ---------------------------------------------------------------------------
class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Enum(RoleName), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_telegram_id", "telegram_id"),
        Index("ix_users_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, nullable=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="uz")
    theme: Mapped[str] = mapped_column(String(8), nullable=False, default="night")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    password_reset_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Wallet balance in minor units (tiyin). Only changed by app.services.wallet
    # under a row lock, always together with a WalletTransaction ledger row.
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    role: Mapped[Role] = relationship(back_populates="users")
    orders: Mapped[list["Order"]] = relationship(back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role is not None and self.role.name == RoleName.ADMIN

    @property
    def is_staff(self) -> bool:
        return (
            self.role is not None
            and self.role.name in (RoleName.ADMIN, RoleName.SUPPORT)
        )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_token_hash", "token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TelegramUser(Base):
    __tablename__ = "telegram_users"
    __table_args__ = (Index("ix_telegram_users_telegram_id", "telegram_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="uz")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_games_slug"),
        Index("ix_games_active", "active"),
        Index("ix_games_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # canonical name
    name_uz: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    name_ru: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    description_uz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="games")
    currency_label: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    icon_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    banner_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    supplier_game_key: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    products: Mapped[list["Product"]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_products_slug"),
        Index("ix_products_game_id", "game_id"),
        Index("ix_products_active", "active"),
        Index("ix_products_type", "product_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    name_uz: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    name_ru: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    description_uz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_type: Mapped[str] = mapped_column(
        Enum(ProductType), nullable=False, default=ProductType.TOPUP
    )
    fulfillment_type: Mapped[str] = mapped_column(
        Enum(FulfillmentType), nullable=False, default=FulfillmentType.AUTO
    )
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    required_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    margin_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # basis? percent*100
    margin_fixed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # minor units
    visibility: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    game: Mapped[Optional[Game]] = relationship(back_populates="products")
    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        Index("ix_variants_product_id", "product_id"),
        Index("ix_variants_supplier_ids", "supplier_product_id", "supplier_variation_id"),
        Index("ix_variants_active", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)  # e.g. "60 UC"
    name_uz: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    name_ru: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    amount_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    supplier_product_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    supplier_variation_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    supplier_cost: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # minor units
    supplier_currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    price_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # customer price
    price_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="UZS")
    margin_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    margin_fixed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    product: Mapped[Product] = relationship(back_populates="variants")


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------
class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # payerpin
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # non-secret only
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "supplier_product_id", "supplier_variation_id",
            name="uq_supplier_product",
        ),
        Index("ix_supplier_products_game_key", "game_key"),
        Index("ix_supplier_products_active", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    game_key: Mapped[str] = mapped_column(String(96), nullable=False)
    game_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    supplier_product_id: Mapped[str] = mapped_column(String(96), nullable=False)
    supplier_variation_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    name: Mapped[str] = mapped_column(String(191), nullable=False, default="")
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    variant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    supplier_cost: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    required_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    fulfillment_type: Mapped[str] = mapped_column(
        Enum(FulfillmentType), nullable=False, default=FulfillmentType.AUTO
    )
    raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class SupplierTransaction(Base):
    __tablename__ = "supplier_transactions"
    __table_args__ = (
        Index("ix_supplier_tx_order_id", "order_id"),
        Index("ix_supplier_tx_supplier_order_id", "supplier_order_id"),
        Index("ix_supplier_tx_status", "fulfillment_status"),
        Index("ix_supplier_tx_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    supplier_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    supplier_tx_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    supplier_product_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    variation_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    request_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    response_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fulfillment_status: Mapped[str] = mapped_column(
        Enum(FulfillmentStatus), nullable=False, default=FulfillmentStatus.PENDING
    )
    request_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # no secrets
    response_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # no secrets
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    order: Mapped["Order"] = relationship("Order", lazy="joined")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


class CatalogSyncLog(Base):
    __tablename__ = "catalog_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    games_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    products_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variants_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    products_activated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    products_deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
class Cart(Base):
    __tablename__ = "carts"
    __table_args__ = (Index("ix_carts_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    coupon_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (Index("ix_cart_items_cart_id", "cart_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cart_id: Mapped[int] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    player_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    cart: Mapped[Cart] = relationship(back_populates="items")
    variant: Mapped[ProductVariant] = relationship()


# ---------------------------------------------------------------------------
# Orders / payments / fulfillments
# ---------------------------------------------------------------------------
class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_number", name="uq_orders_order_number"),
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        Index("ix_orders_user_id", "user_id"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)  # VYR-YYYYMMDD-000001
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING_PAYMENT
    )
    subtotal_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fee_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profit_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # internal
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="UZS")
    coupon_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    player_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    fulfillments: Mapped[list["Fulfillment"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_order_id", "order_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    variant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(191), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(191), nullable=False)
    game_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_total_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supplier_cost_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # internal
    player_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Optional[Product]] = relationship()
    variant: Mapped[Optional[ProductVariant]] = relationship()


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        Index("ix_payments_order_id", "order_id"),
        Index("ix_payments_status", "status"),
        Index("ix_payments_provider_payment_id", "provider_payment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="UZS")
    status: Mapped[str] = mapped_column(
        Enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING
    )
    checkout_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    order: Mapped[Order] = relationship(back_populates="payments")
    transactions: Mapped[list["PaymentTransaction"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint("provider_event_id", name="uq_payment_tx_provider_event"),
        Index("ix_payment_tx_payment_id", "payment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="WEBHOOK")
    provider_event_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED")
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # sanitized
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    payment: Mapped[Payment] = relationship(back_populates="transactions")


class Fulfillment(Base):
    __tablename__ = "fulfillments"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_fulfillments_order_id"),
        Index("ix_fulfillments_status", "status"),
        Index("ix_fulfillments_next_attempt", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(FulfillmentStatus), nullable=False, default=FulfillmentStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    locked_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    order: Mapped[Order] = relationship(back_populates="fulfillments")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery"),
        Index("ix_webhook_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Marketing
# ---------------------------------------------------------------------------
class CouponCode(Base):
    __tablename__ = "coupon_codes"
    __table_args__ = (UniqueConstraint("code", name="uq_coupon_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    coupon_type: Mapped[str] = mapped_column(
        Enum(CouponType), nullable=False, default=CouponType.PERCENT
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # percent or minor
    min_order_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_user_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allow_below_margin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class Promotion(Base):
    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    title_uz: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    title_ru: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    description_uz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    coupon_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


# ---------------------------------------------------------------------------
# Account marketplace (moderated, never auto-delivered)
# ---------------------------------------------------------------------------
class AccountListing(Base):
    __tablename__ = "account_listings"
    __table_args__ = (
        Index("ix_account_listings_status", "status"),
        Index("ix_account_listings_seller", "seller_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    seller_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(191), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="UZS")
    screenshots: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    delivery_method: Mapped[str] = mapped_column(String(32), nullable=False, default="MANUAL")
    status: Mapped[str] = mapped_column(
        Enum(ListingStatus), nullable=False, default=ListingStatus.PENDING_APPROVAL
    )
    moderation_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id", "user_id"),
        Index("ix_notifications_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, default="info")
    title_key: Mapped[str] = mapped_column(String(96), nullable=False)
    body_key: Mapped[str] = mapped_column(String(96), nullable=False)
    params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_admin_user_id", "admin_user_id"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # never secrets
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AdminSetting(Base):
    __tablename__ = "admin_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


class Counter(Base):
    """Transactional counters (daily order numbering)."""

    __tablename__ = "counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Wallet (balance) + Hamyon top-ups
# ---------------------------------------------------------------------------
class WalletTransaction(Base):
    """Immutable wallet ledger. balance_after allows full reconciliation."""

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_wallet_tx_reference"),
        Index("ix_wallet_tx_user_id", "user_id"),
        Index("ix_wallet_tx_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Enum(WalletTxKind), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # signed, minor units
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # unique idempotency reference, e.g. "topup:12", "order:VYR-...", "refund:VYR-..."
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    admin_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class TopUp(Base):
    """A wallet top-up paid by card through Hamyon API (HUMO / UZCARD)."""

    __tablename__ = "topups"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_topups_provider_payment"),
        UniqueConstraint("reference", name="uq_topups_reference"),
        Index("ix_topups_user_id", "user_id"),
        Index("ix_topups_status", "status"),
        Index("ix_topups_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(48), nullable=False)  # TOP-YYYYMMDD-xxxxxx
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="hamyon")
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    requested_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # so'm
    pay_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # exact so'm to transfer
    credited_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # minor
    card_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    card_holder: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(TopUpStatus), nullable=False, default=TopUpStatus.PENDING
    )
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    user: Mapped[User] = relationship()

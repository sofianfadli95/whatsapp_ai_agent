"""SQLAlchemy 2.0 declarative models for all design tables.

Uses mapped_column with type annotations, pgvector Vector type for embeddings,
CHECK constraints, partial unique indexes, and foreign key relationships as
specified in the design document.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover – allow import without pgvector installed
    Vector = None  # type: ignore[assignment,misc]


# Portable JSON type: uses JSONB on PostgreSQL, JSON on other dialects (e.g. SQLite for tests)
PortableJSON = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # Relationships
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="customer")
    carts: Mapped[list["Cart"]] = relationship(back_populates="customer")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    escalation_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_conversations_customer_id"),
        Index("ix_conversations_escalation_flag", "escalation_flag"),
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(back_populates="conversations")
    messages_inbound: Mapped[list["MessageInbound"]] = relationship(back_populates="conversation")
    messages_outbound: Mapped[list["MessageOutbound"]] = relationship(back_populates="conversation")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="conversation")


# ---------------------------------------------------------------------------
# messages_inbound
# ---------------------------------------------------------------------------


class MessageInbound(Base):
    __tablename__ = "messages_inbound"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )
    baileys_message_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    from_phone_raw: Mapped[str] = mapped_column(Text, nullable=False)
    from_phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_type: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    raw_payload: Mapped[dict] = mapped_column(PortableJSON, nullable=False)  # type: ignore[assignment]

    __table_args__ = (
        Index("ix_messages_inbound_conversation_received", "conversation_id", "received_at"),
    )

    # Relationships
    conversation: Mapped["Conversation | None"] = relationship(back_populates="messages_inbound")


# ---------------------------------------------------------------------------
# messages_outbound
# ---------------------------------------------------------------------------


class MessageOutbound(Base):
    __tablename__ = "messages_outbound"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    to_phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    whatsapp_message_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'delivered', 'read')",
            name="ck_messages_outbound_status",
        ),
        Index("ix_messages_outbound_conversation_created", "conversation_id", "created_at"),
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages_outbound")


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # Relationships
    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="product")
    embeddings: Mapped[list["ProductEmbedding"]] = relationship(back_populates="product")


# ---------------------------------------------------------------------------
# product_variants
# ---------------------------------------------------------------------------


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    variant_sku: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    attributes: Mapped[dict] = mapped_column(PortableJSON, nullable=False)  # type: ignore[assignment]
    current_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    available_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("current_price >= 0", name="ck_product_variants_price_non_negative"),
        CheckConstraint("available_stock >= 0", name="ck_product_variants_stock_non_negative"),
        Index("ix_product_variants_product_id", "product_id"),
        Index("ix_product_variants_current_price", "current_price"),
        Index("ix_product_variants_available_stock", "available_stock"),
    )

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="variants")


# ---------------------------------------------------------------------------
# product_embeddings
# ---------------------------------------------------------------------------


class ProductEmbedding(Base):
    __tablename__ = "product_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Vector column – dimension set at migration time; use 1536 as default (OpenAI ada-002)
    embedding = mapped_column(Vector(1536), nullable=True) if Vector else mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("product_id", "chunk_index", name="uq_product_embeddings_product_chunk"),
    )

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="embeddings")


# ---------------------------------------------------------------------------
# faq_documents
# ---------------------------------------------------------------------------


class FaqDocument(Base):
    __tablename__ = "faq_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # Relationships
    embeddings: Mapped[list["FaqEmbedding"]] = relationship(back_populates="faq_document")


# ---------------------------------------------------------------------------
# faq_embeddings
# ---------------------------------------------------------------------------


class FaqEmbedding(Base):
    __tablename__ = "faq_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    faq_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faq_documents.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=True) if Vector else mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("faq_document_id", "chunk_index", name="uq_faq_embeddings_document_chunk"),
    )

    # Relationships
    faq_document: Mapped["FaqDocument"] = relationship(back_populates="embeddings")


# ---------------------------------------------------------------------------
# carts
# ---------------------------------------------------------------------------


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    confirmation_token_invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'CONVERTED', 'EXPIRED', 'ABANDONED')",
            name="ck_carts_status",
        ),
        # Partial unique index: at most one OPEN cart per customer
        Index(
            "uq_carts_customer_open",
            "customer_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(back_populates="carts")
    items: Mapped[list["CartItem"]] = relationship(back_populates="cart")
    # Note: order relationship via order_id is independent (no back_populates)
    # because Order also has cart_id FK creating a circular reference.
    order: Mapped["Order | None"] = relationship(
        foreign_keys=[order_id], uselist=False
    )


# ---------------------------------------------------------------------------
# cart_items
# ---------------------------------------------------------------------------


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "quantity >= 1 AND quantity <= 999",
            name="ck_cart_items_quantity_range",
        ),
        CheckConstraint(
            "unit_price_snapshot >= 0",
            name="ck_cart_items_price_non_negative",
        ),
        UniqueConstraint("cart_id", "variant_id", name="uq_cart_items_cart_variant"),
        Index("ix_cart_items_cart_id", "cart_id"),
    )

    # Relationships
    cart: Mapped["Cart"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()
    variant: Mapped["ProductVariant"] = relationship()


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    backend_fees: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipment_prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_payment', 'paid', 'shipment_prepared', 'cancelled')",
            name="ck_orders_status",
        ),
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        Index("ix_orders_status", "status"),
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(back_populates="orders")
    cart: Mapped["Cart"] = relationship(
        foreign_keys=[cart_id]
    )
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    shipment: Mapped["Shipment | None"] = relationship(back_populates="order", uselist=False)
    dispatched_actions: Mapped[list["DispatchedAction"]] = relationship(back_populates="order")


# ---------------------------------------------------------------------------
# order_items
# ---------------------------------------------------------------------------


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    __table_args__ = (
        Index("ix_order_items_order_id", "order_id"),
    )

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()
    variant: Mapped["ProductVariant"] = relationship()


# ---------------------------------------------------------------------------
# payments
# ---------------------------------------------------------------------------


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_ref: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_metadata: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  # type: ignore[assignment]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'paid', 'failed', 'expired')",
            name="ck_payments_status",
        ),
        UniqueConstraint("provider", "provider_ref", name="uq_payments_provider_ref"),
        Index("ix_payments_order_id", "order_id"),
    )

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="payments")
    webhook_events: Mapped[list["PaymentWebhookEvent"]] = relationship(back_populates="payment")


# ---------------------------------------------------------------------------
# payment_webhook_events
# ---------------------------------------------------------------------------


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    webhook_event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True
    )
    event_kind: Mapped[str] = mapped_column(Text, nullable=False)
    signature_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(PortableJSON, nullable=False)  # type: ignore[assignment]
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "event_kind IN ('paid', 'failed', 'expired', 'unmatched')",
            name="ck_payment_webhook_events_kind",
        ),
        Index("ix_payment_webhook_events_payment_id", "payment_id"),
    )

    # Relationships
    payment: Mapped["Payment | None"] = relationship(back_populates="webhook_events")


# ---------------------------------------------------------------------------
# shipments
# ---------------------------------------------------------------------------


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True
    )
    tracking_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared', 'in_transit', 'delivered', 'failed')",
            name="ck_shipments_status",
        ),
        CheckConstraint(
            "length(tracking_number) >= 8 AND length(tracking_number) <= 32",
            name="ck_shipments_tracking_number_length",
        ),
    )

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="shipment")


# ---------------------------------------------------------------------------
# dispatched_actions
# ---------------------------------------------------------------------------


class DispatchedAction(Base):
    __tablename__ = "dispatched_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('logistics_prepare', 'payment_confirmation_reply', 'tracking_notification')",
            name="ck_dispatched_actions_type",
        ),
        UniqueConstraint("order_id", "action_type", name="uq_dispatched_actions_order_action"),
    )

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="dispatched_actions")


# ---------------------------------------------------------------------------
# audit_logs
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_redacted: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  # type: ignore[assignment]
    output_redacted: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  # type: ignore[assignment]
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index(
            "uq_audit_logs_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
        Index("ix_audit_logs_occurred_at", "occurred_at"),
        Index("ix_audit_logs_conversation_id", "conversation_id"),
        Index("ix_audit_logs_order_id", "order_id"),
    )


# ---------------------------------------------------------------------------
# audit_log_failures
# ---------------------------------------------------------------------------


class AuditLogFailure(Base):
    __tablename__ = "audit_log_failures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_redacted: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  # type: ignore[assignment]
    output_redacted: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  # type: ignore[assignment]
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# escalations
# ---------------------------------------------------------------------------


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    context_snapshot: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  # type: ignore[assignment]
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_escalations_conversation_active", "conversation_id", "is_active"),
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="escalations")


# ---------------------------------------------------------------------------
# admin_users
# ---------------------------------------------------------------------------


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

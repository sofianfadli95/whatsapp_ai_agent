"""Initial schema: all tables, CHECK constraints, partial unique indexes, pgvector indexes.

Revision ID: 0001
Revises: None
Create Date: 2024-01-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- customers ---
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone_e164"),
    )

    # --- conversations ---
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("escalation_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.UniqueConstraint("customer_id", name="uq_conversations_customer_id"),
    )
    op.create_index("ix_conversations_escalation_flag", "conversations", ["escalation_flag"])

    # --- messages_inbound ---
    op.create_table(
        "messages_inbound",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("baileys_message_id", sa.Text(), nullable=False),
        sa.Column("from_phone_raw", sa.Text(), nullable=False),
        sa.Column("from_phone_e164", sa.Text(), nullable=True),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint("baileys_message_id"),
    )
    op.create_index(
        "ix_messages_inbound_conversation_received",
        "messages_inbound",
        ["conversation_id", "received_at"],
    )

    # --- messages_outbound ---
    op.create_table(
        "messages_outbound",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_phone_e164", sa.Text(), nullable=False),
        sa.Column("whatsapp_message_id", sa.Text(), nullable=True),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint("whatsapp_message_id"),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'delivered', 'read')",
            name="ck_messages_outbound_status",
        ),
    )
    op.create_index(
        "ix_messages_outbound_conversation_created",
        "messages_outbound",
        ["conversation_id", "created_at"],
    )

    # --- products ---
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )

    # --- product_variants ---
    op.create_table(
        "product_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_sku", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column("current_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("available_stock", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.UniqueConstraint("variant_sku"),
        sa.CheckConstraint("current_price >= 0", name="ck_product_variants_price_non_negative"),
        sa.CheckConstraint("available_stock >= 0", name="ck_product_variants_stock_non_negative"),
    )
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])
    op.create_index("ix_product_variants_current_price", "product_variants", ["current_price"])
    op.create_index("ix_product_variants_available_stock", "product_variants", ["available_stock"])

    # --- product_embeddings ---
    op.create_table(
        "product_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),  # placeholder, replaced by raw SQL below
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.UniqueConstraint("product_id", "chunk_index", name="uq_product_embeddings_product_chunk"),
    )
    # Replace the placeholder text column with a proper vector column
    op.execute("ALTER TABLE product_embeddings DROP COLUMN embedding")
    op.execute("ALTER TABLE product_embeddings ADD COLUMN embedding vector(1536)")

    # --- faq_documents ---
    op.create_table(
        "faq_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- faq_embeddings ---
    op.create_table(
        "faq_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("faq_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),  # placeholder, replaced by raw SQL below
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["faq_document_id"], ["faq_documents.id"]),
        sa.UniqueConstraint("faq_document_id", "chunk_index", name="uq_faq_embeddings_document_chunk"),
    )
    # Replace the placeholder text column with a proper vector column
    op.execute("ALTER TABLE faq_embeddings DROP COLUMN embedding")
    op.execute("ALTER TABLE faq_embeddings ADD COLUMN embedding vector(1536)")

    # --- orders (created before carts due to FK from carts.order_id) ---
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("backend_fees", sa.Numeric(precision=18, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipment_prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        # cart_id FK added after carts table is created
        sa.CheckConstraint(
            "status IN ('pending_payment', 'paid', 'shipment_prepared', 'cancelled')",
            name="ck_orders_status",
        ),
    )
    op.create_index("ix_orders_customer_created", "orders", ["customer_id", "created_at"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # --- carts ---
    op.create_table(
        "carts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=18, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("confirmation_token_invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CONVERTED', 'EXPIRED', 'ABANDONED')",
            name="ck_carts_status",
        ),
    )
    # Partial unique index: at most one OPEN cart per customer
    op.execute(
        "CREATE UNIQUE INDEX uq_carts_customer_open ON carts (customer_id) WHERE status = 'OPEN'"
    )

    # Now add the cart_id FK on orders and unique constraint
    op.create_foreign_key("fk_orders_cart_id", "orders", "carts", ["cart_id"], ["id"])
    op.create_unique_constraint("uq_orders_cart_id", "orders", ["cart_id"])

    # --- cart_items ---
    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_snapshot", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"]),
        sa.UniqueConstraint("cart_id", "variant_id", name="uq_cart_items_cart_variant"),
        sa.CheckConstraint(
            "quantity >= 1 AND quantity <= 999",
            name="ck_cart_items_quantity_range",
        ),
        sa.CheckConstraint(
            "unit_price_snapshot >= 0",
            name="ck_cart_items_price_non_negative",
        ),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])

    # --- order_items ---
    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_snapshot", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"]),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    # --- payments ---
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=False),
        sa.Column("link_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_payments_provider_ref"),
        sa.CheckConstraint(
            "status IN ('created', 'paid', 'failed', 'expired')",
            name="ck_payments_status",
        ),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"])

    # --- payment_webhook_events ---
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("webhook_event_id", sa.Text(), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("signature_header", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.UniqueConstraint("webhook_event_id"),
        sa.CheckConstraint(
            "event_kind IN ('paid', 'failed', 'expired', 'unmatched')",
            name="ck_payment_webhook_events_kind",
        ),
    )
    op.create_index("ix_payment_webhook_events_payment_id", "payment_webhook_events", ["payment_id"])

    # --- shipments ---
    op.create_table(
        "shipments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tracking_number", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("tracking_number"),
        sa.CheckConstraint(
            "status IN ('prepared', 'in_transit', 'delivered', 'failed')",
            name="ck_shipments_status",
        ),
        sa.CheckConstraint(
            "length(tracking_number) >= 8 AND length(tracking_number) <= 32",
            name="ck_shipments_tracking_number_length",
        ),
    )

    # --- dispatched_actions ---
    op.create_table(
        "dispatched_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.UniqueConstraint("order_id", "action_type", name="uq_dispatched_actions_order_action"),
        sa.CheckConstraint(
            "action_type IN ('logistics_prepare', 'payment_confirmation_reply', 'tracking_notification')",
            name="ck_dispatched_actions_type",
        ),
    )

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("input_redacted", postgresql.JSONB(), nullable=True),
        sa.Column("output_redacted", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index on dedupe_key where not null
    op.execute(
        "CREATE UNIQUE INDEX uq_audit_logs_dedupe_key ON audit_logs (dedupe_key) WHERE dedupe_key IS NOT NULL"
    )
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.create_index("ix_audit_logs_conversation_id", "audit_logs", ["conversation_id"])
    op.create_index("ix_audit_logs_order_id", "audit_logs", ["order_id"])

    # --- audit_log_failures ---
    op.create_table(
        "audit_log_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("input_redacted", postgresql.JSONB(), nullable=True),
        sa.Column("output_redacted", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- escalations ---
    op.create_table(
        "escalations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("context_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index("ix_escalations_conversation_active", "escalations", ["conversation_id", "is_active"])

    # --- admin_users ---
    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    # --- pgvector indexes (HNSW) on embedding columns ---
    # HNSW index for product_embeddings.embedding (cosine distance)
    op.execute(
        "CREATE INDEX ix_product_embeddings_embedding_hnsw ON product_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    # HNSW index for faq_embeddings.embedding (cosine distance)
    op.execute(
        "CREATE INDEX ix_faq_embeddings_embedding_hnsw ON faq_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("admin_users")
    op.drop_table("escalations")
    op.drop_table("audit_log_failures")
    op.execute("DROP INDEX IF EXISTS uq_audit_logs_dedupe_key")
    op.drop_table("audit_logs")
    op.drop_table("dispatched_actions")
    op.drop_table("shipments")
    op.drop_table("payment_webhook_events")
    op.drop_table("payments")
    op.drop_table("order_items")
    op.drop_table("cart_items")
    # Drop the partial unique index before dropping carts
    op.execute("DROP INDEX IF EXISTS uq_carts_customer_open")
    # Drop the FK and unique constraint on orders.cart_id before dropping carts
    op.drop_constraint("fk_orders_cart_id", "orders", type_="foreignkey")
    op.drop_constraint("uq_orders_cart_id", "orders", type_="unique")
    op.drop_table("carts")
    op.drop_table("orders")
    # Drop pgvector indexes
    op.execute("DROP INDEX IF EXISTS ix_faq_embeddings_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_product_embeddings_embedding_hnsw")
    op.drop_table("faq_embeddings")
    op.drop_table("faq_documents")
    op.drop_table("product_embeddings")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("messages_outbound")
    op.drop_table("messages_inbound")
    op.drop_table("conversations")
    op.drop_table("customers")
    # Drop the vector extension
    op.execute("DROP EXTENSION IF EXISTS vector")

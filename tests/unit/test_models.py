"""Unit tests for app/db/models.py — round-trip insert/select on each model.

Validates that all SQLAlchemy models can be instantiated, inserted, and
selected from an in-memory SQLite database. This verifies the ORM mapping,
column types, and basic relationships are correctly defined.

Requirements: 1, 2, 3, 5, 6, 7, 8, 9, 10, 11
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    AdminUser,
    AuditLog,
    AuditLogFailure,
    Base,
    Cart,
    CartItem,
    Conversation,
    Customer,
    DispatchedAction,
    Escalation,
    FaqDocument,
    FaqEmbedding,
    MessageInbound,
    MessageOutbound,
    Order,
    OrderItem,
    Payment,
    PaymentWebhookEvent,
    Product,
    ProductEmbedding,
    ProductVariant,
    Shipment,
)

_TEST_DB_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture
async def session():
    """Create an in-memory SQLite database with all tables and yield a session."""
    engine = create_async_engine(_TEST_DB_URL, echo=False)

    # SQLite doesn't enforce CHECK constraints by default; enable them
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    await engine.dispose()


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestCustomer:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        customer = Customer(id=cid, phone_e164="+6281234567890", created_at=_now(), updated_at=_now())
        session.add(customer)
        await session.commit()

        result = await session.get(Customer, cid)
        assert result is not None
        assert result.phone_e164 == "+6281234567890"


class TestConversation:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6281111111111", created_at=_now(), updated_at=_now()))
        await session.flush()

        conv_id = _uuid()
        conv = Conversation(
            id=conv_id, customer_id=cid, escalation_flag=False,
            created_at=_now(), updated_at=_now()
        )
        session.add(conv)
        await session.commit()

        result = await session.get(Conversation, conv_id)
        assert result is not None
        assert result.customer_id == cid
        assert result.escalation_flag is False


class TestMessageInbound:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6282222222222", created_at=_now(), updated_at=_now()))
        await session.flush()

        conv_id = _uuid()
        session.add(Conversation(id=conv_id, customer_id=cid, escalation_flag=False, created_at=_now(), updated_at=_now()))
        await session.flush()

        msg_id = _uuid()
        msg = MessageInbound(
            id=msg_id,
            conversation_id=conv_id,
            baileys_message_id="BAE5_UNIQUE_123",
            from_phone_raw="081234567890",
            from_phone_e164="+6281234567890",
            message_type="text",
            text_body="Hello",
            event_timestamp=_now(),
            received_at=_now(),
            raw_payload={"key": "value"},
        )
        session.add(msg)
        await session.commit()

        result = await session.get(MessageInbound, msg_id)
        assert result is not None
        assert result.baileys_message_id == "BAE5_UNIQUE_123"
        assert result.message_type == "text"


class TestMessageOutbound:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6283333333333", created_at=_now(), updated_at=_now()))
        await session.flush()

        conv_id = _uuid()
        session.add(Conversation(id=conv_id, customer_id=cid, escalation_flag=False, created_at=_now(), updated_at=_now()))
        await session.flush()

        msg_id = _uuid()
        msg = MessageOutbound(
            id=msg_id,
            conversation_id=conv_id,
            to_phone_e164="+6283333333333",
            text_body="Hi there!",
            status="queued",
            attempts=0,
            created_at=_now(),
        )
        session.add(msg)
        await session.commit()

        result = await session.get(MessageOutbound, msg_id)
        assert result is not None
        assert result.status == "queued"
        assert result.text_body == "Hi there!"


class TestProduct:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        pid = _uuid()
        product = Product(
            id=pid, sku="SKU-001", name="Test Product",
            description="A test product", category="skincare",
            is_active=True, created_at=_now(), updated_at=_now()
        )
        session.add(product)
        await session.commit()

        result = await session.get(Product, pid)
        assert result is not None
        assert result.sku == "SKU-001"
        assert result.name == "Test Product"


class TestProductVariant:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        pid = _uuid()
        session.add(Product(id=pid, sku="SKU-002", name="Prod", description="Desc", created_at=_now(), updated_at=_now()))
        await session.flush()

        vid = _uuid()
        variant = ProductVariant(
            id=vid, product_id=pid, variant_sku="SKU-002-BLK-M",
            attributes={"color": "black", "size": "M"},
            current_price=Decimal("150000.0000"), currency="IDR",
            available_stock=50, created_at=_now(), updated_at=_now()
        )
        session.add(variant)
        await session.commit()

        result = await session.get(ProductVariant, vid)
        assert result is not None
        assert result.variant_sku == "SKU-002-BLK-M"
        assert result.current_price == Decimal("150000.0000")
        assert result.available_stock == 50


class TestProductEmbedding:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        pid = _uuid()
        session.add(Product(id=pid, sku="SKU-EMB", name="Emb Prod", description="Desc", created_at=_now(), updated_at=_now()))
        await session.flush()

        eid = _uuid()
        emb = ProductEmbedding(
            id=eid, product_id=pid, chunk_index=0,
            chunk_text="This is a chunk of text about the product.",
            updated_at=_now()
        )
        session.add(emb)
        await session.commit()

        result = await session.get(ProductEmbedding, eid)
        assert result is not None
        assert result.chunk_index == 0
        assert result.chunk_text.startswith("This is a chunk")


class TestFaqDocument:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        fid = _uuid()
        faq = FaqDocument(
            id=fid, title="Return Policy", body="You can return within 7 days.",
            is_active=True, created_at=_now(), updated_at=_now()
        )
        session.add(faq)
        await session.commit()

        result = await session.get(FaqDocument, fid)
        assert result is not None
        assert result.title == "Return Policy"


class TestFaqEmbedding:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        fid = _uuid()
        session.add(FaqDocument(id=fid, title="FAQ", body="Body", is_active=True, created_at=_now(), updated_at=_now()))
        await session.flush()

        eid = _uuid()
        emb = FaqEmbedding(
            id=eid, faq_document_id=fid, chunk_index=0,
            chunk_text="FAQ chunk text", updated_at=_now()
        )
        session.add(emb)
        await session.commit()

        result = await session.get(FaqEmbedding, eid)
        assert result is not None
        assert result.chunk_text == "FAQ chunk text"


class TestCart:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6284444444444", created_at=_now(), updated_at=_now()))
        await session.flush()

        cart_id = _uuid()
        cart = Cart(
            id=cart_id, customer_id=cid, status="OPEN",
            subtotal=Decimal("0"), currency="IDR",
            created_at=_now(), updated_at=_now()
        )
        session.add(cart)
        await session.commit()

        result = await session.get(Cart, cart_id)
        assert result is not None
        assert result.status == "OPEN"
        assert result.currency == "IDR"


class TestCartItem:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6285555555555", created_at=_now(), updated_at=_now()))
        await session.flush()

        pid = _uuid()
        session.add(Product(id=pid, sku="SKU-CART", name="Cart Prod", description="D", created_at=_now(), updated_at=_now()))
        await session.flush()

        vid = _uuid()
        session.add(ProductVariant(
            id=vid, product_id=pid, variant_sku="SKU-CART-V1",
            attributes={}, current_price=Decimal("50000"), currency="IDR",
            available_stock=10, created_at=_now(), updated_at=_now()
        ))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="OPEN", subtotal=Decimal("50000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        item_id = _uuid()
        item = CartItem(
            id=item_id, cart_id=cart_id, product_id=pid, variant_id=vid,
            quantity=2, unit_price_snapshot=Decimal("50000"), currency="IDR",
            created_at=_now(), updated_at=_now()
        )
        session.add(item)
        await session.commit()

        result = await session.get(CartItem, item_id)
        assert result is not None
        assert result.quantity == 2
        assert result.unit_price_snapshot == Decimal("50000")


class TestOrder:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6286666666666", created_at=_now(), updated_at=_now()))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="CONVERTED", subtotal=Decimal("100000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        order_id = _uuid()
        order = Order(
            id=order_id, customer_id=cid, cart_id=cart_id,
            status="pending_payment", total=Decimal("100000"),
            currency="IDR", backend_fees=Decimal("0"),
            created_at=_now()
        )
        session.add(order)
        await session.commit()

        result = await session.get(Order, order_id)
        assert result is not None
        assert result.status == "pending_payment"
        assert result.total == Decimal("100000")


class TestOrderItem:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6287777777777", created_at=_now(), updated_at=_now()))
        await session.flush()

        pid = _uuid()
        session.add(Product(id=pid, sku="SKU-OI", name="OI Prod", description="D", created_at=_now(), updated_at=_now()))
        await session.flush()

        vid = _uuid()
        session.add(ProductVariant(
            id=vid, product_id=pid, variant_sku="SKU-OI-V1",
            attributes={}, current_price=Decimal("75000"), currency="IDR",
            available_stock=5, created_at=_now(), updated_at=_now()
        ))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="CONVERTED", subtotal=Decimal("75000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        order_id = _uuid()
        session.add(Order(id=order_id, customer_id=cid, cart_id=cart_id, status="pending_payment", total=Decimal("75000"), currency="IDR", backend_fees=Decimal("0"), created_at=_now()))
        await session.flush()

        oi_id = _uuid()
        oi = OrderItem(
            id=oi_id, order_id=order_id, product_id=pid, variant_id=vid,
            quantity=1, unit_price_snapshot=Decimal("75000"),
            currency="IDR", line_total=Decimal("75000")
        )
        session.add(oi)
        await session.commit()

        result = await session.get(OrderItem, oi_id)
        assert result is not None
        assert result.line_total == Decimal("75000")


class TestPayment:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6288888888888", created_at=_now(), updated_at=_now()))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="CONVERTED", subtotal=Decimal("200000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        order_id = _uuid()
        session.add(Order(id=order_id, customer_id=cid, cart_id=cart_id, status="pending_payment", total=Decimal("200000"), currency="IDR", backend_fees=Decimal("0"), created_at=_now()))
        await session.flush()

        pay_id = _uuid()
        payment = Payment(
            id=pay_id, order_id=order_id, provider="xendit",
            provider_ref="xnd_ref_123", link_url="https://pay.example.com/abc",
            status="created", amount=Decimal("200000"), currency="IDR",
            expires_at=_now(), created_at=_now(), updated_at=_now()
        )
        session.add(payment)
        await session.commit()

        result = await session.get(Payment, pay_id)
        assert result is not None
        assert result.provider == "xendit"
        assert result.status == "created"


class TestPaymentWebhookEvent:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6289999999999", created_at=_now(), updated_at=_now()))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="CONVERTED", subtotal=Decimal("100000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        order_id = _uuid()
        session.add(Order(id=order_id, customer_id=cid, cart_id=cart_id, status="pending_payment", total=Decimal("100000"), currency="IDR", backend_fees=Decimal("0"), created_at=_now()))
        await session.flush()

        pay_id = _uuid()
        session.add(Payment(id=pay_id, order_id=order_id, provider="xendit", provider_ref="xnd_wh_1", link_url="https://pay.example.com/x", status="created", amount=Decimal("100000"), currency="IDR", expires_at=_now(), created_at=_now(), updated_at=_now()))
        await session.flush()

        whe_id = _uuid()
        whe = PaymentWebhookEvent(
            id=whe_id, webhook_event_id="evt_abc123",
            payment_id=pay_id, event_kind="paid",
            raw_payload={"event": "paid"},
            received_at=_now(), processed_at=_now()
        )
        session.add(whe)
        await session.commit()

        result = await session.get(PaymentWebhookEvent, whe_id)
        assert result is not None
        assert result.webhook_event_id == "evt_abc123"
        assert result.event_kind == "paid"


class TestShipment:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6281010101010", created_at=_now(), updated_at=_now()))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="CONVERTED", subtotal=Decimal("50000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        order_id = _uuid()
        session.add(Order(id=order_id, customer_id=cid, cart_id=cart_id, status="paid", total=Decimal("50000"), currency="IDR", backend_fees=Decimal("0"), created_at=_now()))
        await session.flush()

        ship_id = _uuid()
        shipment = Shipment(
            id=ship_id, order_id=order_id,
            tracking_number="TRK12345678",
            status="prepared",
            created_at=_now(), updated_at=_now()
        )
        session.add(shipment)
        await session.commit()

        result = await session.get(Shipment, ship_id)
        assert result is not None
        assert result.tracking_number == "TRK12345678"
        assert result.status == "prepared"


class TestDispatchedAction:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6281020202020", created_at=_now(), updated_at=_now()))
        await session.flush()

        cart_id = _uuid()
        session.add(Cart(id=cart_id, customer_id=cid, status="CONVERTED", subtotal=Decimal("50000"), currency="IDR", created_at=_now(), updated_at=_now()))
        await session.flush()

        order_id = _uuid()
        session.add(Order(id=order_id, customer_id=cid, cart_id=cart_id, status="paid", total=Decimal("50000"), currency="IDR", backend_fees=Decimal("0"), created_at=_now()))
        await session.flush()

        da_id = _uuid()
        da = DispatchedAction(
            id=da_id, order_id=order_id,
            action_type="logistics_prepare",
            dispatched_at=_now()
        )
        session.add(da)
        await session.commit()

        result = await session.get(DispatchedAction, da_id)
        assert result is not None
        assert result.action_type == "logistics_prepare"


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        al_id = _uuid()
        al = AuditLog(
            id=al_id, actor="agent", event_type="tool_invocation",
            tool_name="search_products",
            input_redacted={"query": "moisturizer"},
            output_redacted={"count": 3},
            occurred_at=_now()
        )
        session.add(al)
        await session.commit()

        result = await session.get(AuditLog, al_id)
        assert result is not None
        assert result.actor == "agent"
        assert result.event_type == "tool_invocation"


class TestAuditLogFailure:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        alf_id = _uuid()
        alf = AuditLogFailure(
            id=alf_id, actor="system", event_type="tool_invocation",
            tool_name="add_to_cart",
            occurred_at=_now(),
            attempt_count=3,
            last_error="connection timeout",
            last_attempt_at=_now()
        )
        session.add(alf)
        await session.commit()

        result = await session.get(AuditLogFailure, alf_id)
        assert result is not None
        assert result.attempt_count == 3
        assert result.last_error == "connection timeout"


class TestEscalation:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        cid = _uuid()
        session.add(Customer(id=cid, phone_e164="+6281030303030", created_at=_now(), updated_at=_now()))
        await session.flush()

        conv_id = _uuid()
        session.add(Conversation(id=conv_id, customer_id=cid, escalation_flag=True, created_at=_now(), updated_at=_now()))
        await session.flush()

        esc_id = _uuid()
        esc = Escalation(
            id=esc_id, conversation_id=conv_id,
            reason="customer_complaint", actor="agent",
            context_snapshot={"messages": []},
            is_active=True, set_at=_now()
        )
        session.add(esc)
        await session.commit()

        result = await session.get(Escalation, esc_id)
        assert result is not None
        assert result.reason == "customer_complaint"
        assert result.is_active is True


class TestAdminUser:
    @pytest.mark.asyncio
    async def test_round_trip(self, session: AsyncSession):
        admin_id = _uuid()
        admin = AdminUser(
            id=admin_id, email="admin@example.com",
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$...",
            is_active=True, created_at=_now(), updated_at=_now()
        )
        session.add(admin)
        await session.commit()

        result = await session.get(AdminUser, admin_id)
        assert result is not None
        assert result.email == "admin@example.com"
        assert result.is_active is True

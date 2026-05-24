"""Unit tests for app/repositories/ — all repository modules.

Uses SQLite async (aiosqlite) with a transactional rollback fixture.
Each repo function has at least one test.

Validates: Requirements 1, 5, 6, 7, 8, 9, 10, 11
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

# ---------------------------------------------------------------------------
# Transactional rollback fixture
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture
async def session():
    """Create all tables, yield a session, then drop everything (rollback)."""
    engine = create_async_engine(_TEST_DB_URL, echo=False)

    # SQLite doesn't support FOR UPDATE — make it a no-op
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    # Teardown: drop all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Tests: customers
# ---------------------------------------------------------------------------


class TestCustomersRepo:
    @pytest.mark.asyncio
    async def test_upsert_creates_new_customer(self, session: AsyncSession):
        from app.repositories.customers import upsert

        customer = await upsert(session, "+6281234567890")
        await session.commit()

        assert customer is not None
        assert customer.phone_e164 == "+6281234567890"
        assert customer.id is not None

    @pytest.mark.asyncio
    async def test_upsert_returns_existing_customer(self, session: AsyncSession):
        from app.repositories.customers import upsert

        c1 = await upsert(session, "+6281234567890")
        await session.commit()
        c2 = await upsert(session, "+6281234567890")
        await session.commit()

        assert c1.id == c2.id

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession):
        from app.repositories.customers import upsert, get_by_id

        customer = await upsert(session, "+6281234567890")
        await session.commit()

        found = await get_by_id(session, customer.id)
        assert found is not None
        assert found.phone_e164 == "+6281234567890"

    @pytest.mark.asyncio
    async def test_get_by_phone(self, session: AsyncSession):
        from app.repositories.customers import upsert, get_by_phone

        await upsert(session, "+6281234567890")
        await session.commit()

        found = await get_by_phone(session, "+6281234567890")
        assert found is not None
        assert found.phone_e164 == "+6281234567890"

        not_found = await get_by_phone(session, "+6289999999999")
        assert not_found is None


# ---------------------------------------------------------------------------
# Tests: conversations
# ---------------------------------------------------------------------------


class TestConversationsRepo:
    @pytest.mark.asyncio
    async def test_get_or_create_creates_new(self, session: AsyncSession):
        from app.repositories.customers import upsert as upsert_customer
        from app.repositories.conversations import get_or_create_by_customer

        customer = await upsert_customer(session, "+6281234567890")
        await session.commit()

        conv = await get_or_create_by_customer(session, customer.id)
        await session.commit()

        assert conv is not None
        assert conv.customer_id == customer.id
        assert conv.escalation_flag is False

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing(self, session: AsyncSession):
        from app.repositories.customers import upsert as upsert_customer
        from app.repositories.conversations import get_or_create_by_customer

        customer = await upsert_customer(session, "+6281234567890")
        await session.commit()

        conv1 = await get_or_create_by_customer(session, customer.id)
        await session.commit()
        conv2 = await get_or_create_by_customer(session, customer.id)
        await session.commit()

        assert conv1.id == conv2.id

    @pytest.mark.asyncio
    async def test_set_and_clear_escalation(self, session: AsyncSession):
        from app.repositories.customers import upsert as upsert_customer
        from app.repositories.conversations import (
            get_or_create_by_customer,
            set_escalation,
            clear_escalation,
            get_by_customer,
        )

        customer = await upsert_customer(session, "+6281234567890")
        await session.commit()
        conv = await get_or_create_by_customer(session, customer.id)
        await session.commit()

        await set_escalation(session, conv.id, "customer_request")
        await session.commit()

        refreshed = await get_by_customer(session, customer.id)
        assert refreshed is not None
        assert refreshed.escalation_flag is True
        assert refreshed.escalation_reason == "customer_request"

        await clear_escalation(session, conv.id)
        await session.commit()

        refreshed = await get_by_customer(session, customer.id)
        assert refreshed is not None
        assert refreshed.escalation_flag is False
        assert refreshed.escalation_reason is None

    @pytest.mark.asyncio
    async def test_update_last_message_at(self, session: AsyncSession):
        from app.repositories.customers import upsert as upsert_customer
        from app.repositories.conversations import (
            get_or_create_by_customer,
            update_last_message_at,
            get_by_customer,
        )

        customer = await upsert_customer(session, "+6281234567890")
        await session.commit()
        conv = await get_or_create_by_customer(session, customer.id)
        await session.commit()

        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        await update_last_message_at(session, conv.id, ts)
        await session.commit()

        refreshed = await get_by_customer(session, customer.id)
        assert refreshed is not None
        # SQLite strips timezone info, so compare naive datetimes
        assert refreshed.last_message_at is not None
        expected_naive = ts.replace(tzinfo=None)
        actual_naive = refreshed.last_message_at.replace(tzinfo=None) if refreshed.last_message_at.tzinfo else refreshed.last_message_at
        assert actual_naive == expected_naive


# ---------------------------------------------------------------------------
# Tests: messages
# ---------------------------------------------------------------------------


class TestMessagesRepo:
    @pytest.mark.asyncio
    async def test_insert_inbound_idempotent_first_insert(self, session: AsyncSession):
        from app.repositories.messages import insert_inbound_idempotent

        msg = await insert_inbound_idempotent(
            session,
            baileys_message_id="msg_001",
            conversation_id=None,
            from_phone_raw="6281234567890",
            from_phone_e164="+6281234567890",
            message_type="text",
            text_body="Hello",
            event_timestamp=_now(),
            raw_payload={"key": "value"},
        )
        await session.commit()

        assert msg is not None
        assert msg.baileys_message_id == "msg_001"
        assert msg.text_body == "Hello"

    @pytest.mark.asyncio
    async def test_insert_inbound_idempotent_duplicate_returns_none(self, session: AsyncSession):
        from app.repositories.messages import insert_inbound_idempotent

        await insert_inbound_idempotent(
            session,
            baileys_message_id="msg_001",
            conversation_id=None,
            from_phone_raw="6281234567890",
            from_phone_e164="+6281234567890",
            message_type="text",
            text_body="Hello",
            event_timestamp=_now(),
            raw_payload={},
        )
        await session.commit()

        # Second insert with same baileys_message_id
        result = await insert_inbound_idempotent(
            session,
            baileys_message_id="msg_001",
            conversation_id=None,
            from_phone_raw="6281234567890",
            from_phone_e164="+6281234567890",
            message_type="text",
            text_body="Hello again",
            event_timestamp=_now(),
            raw_payload={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_insert_outbound(self, session: AsyncSession):
        from app.repositories.customers import upsert as upsert_customer
        from app.repositories.conversations import get_or_create_by_customer
        from app.repositories.messages import insert_outbound

        customer = await upsert_customer(session, "+6281234567890")
        await session.commit()
        conv = await get_or_create_by_customer(session, customer.id)
        await session.commit()

        msg = await insert_outbound(
            session,
            conversation_id=conv.id,
            to_phone_e164="+6281234567890",
            text_body="Hi there!",
        )
        await session.commit()

        assert msg is not None
        assert msg.status == "queued"
        assert msg.text_body == "Hi there!"

    @pytest.mark.asyncio
    async def test_update_outbound_status(self, session: AsyncSession):
        from app.repositories.customers import upsert as upsert_customer
        from app.repositories.conversations import get_or_create_by_customer
        from app.repositories.messages import insert_outbound, update_outbound_status

        customer = await upsert_customer(session, "+6281234567890")
        await session.commit()
        conv = await get_or_create_by_customer(session, customer.id)
        await session.commit()

        msg = await insert_outbound(
            session,
            conversation_id=conv.id,
            to_phone_e164="+6281234567890",
            text_body="Hi there!",
        )
        # Simulate setting the whatsapp_message_id after send
        msg.whatsapp_message_id = "wa_msg_123"
        await session.flush()
        await session.commit()

        updated = await update_outbound_status(
            session,
            whatsapp_message_id="wa_msg_123",
            status="sent",
            sent_at=_now(),
        )
        await session.commit()
        assert updated is True

        # Non-existent message
        not_updated = await update_outbound_status(
            session,
            whatsapp_message_id="nonexistent",
            status="sent",
        )
        assert not_updated is False


# ---------------------------------------------------------------------------
# Tests: products
# ---------------------------------------------------------------------------


class TestProductsRepo:
    @pytest.mark.asyncio
    async def test_get_product_by_id(self, session: AsyncSession):
        from app.repositories.products import get_product_by_id
        from app.db.models import Product

        product = Product(
            id=_uuid(),
            sku="SKU-001",
            name="Test Product",
            description="A test product",
            category="test",
            is_active=True,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(product)
        await session.commit()

        found = await get_product_by_id(session, product.id)
        assert found is not None
        assert found.name == "Test Product"

    @pytest.mark.asyncio
    async def test_get_product_by_sku(self, session: AsyncSession):
        from app.repositories.products import get_product_by_sku
        from app.db.models import Product

        product = Product(
            id=_uuid(),
            sku="SKU-002",
            name="Another Product",
            description="Another test product",
            is_active=True,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(product)
        await session.commit()

        found = await get_product_by_sku(session, "SKU-002")
        assert found is not None
        assert found.name == "Another Product"

        not_found = await get_product_by_sku(session, "NONEXISTENT")
        assert not_found is None

    @pytest.mark.asyncio
    async def test_get_variant_by_id(self, session: AsyncSession):
        from app.repositories.products import get_variant_by_id
        from app.db.models import Product, ProductVariant

        product = Product(
            id=_uuid(),
            sku="SKU-003",
            name="Product With Variant",
            description="Has variants",
            is_active=True,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(product)
        await session.flush()

        variant = ProductVariant(
            id=_uuid(),
            product_id=product.id,
            variant_sku="VAR-001",
            attributes={"color": "red"},
            current_price=Decimal("50000.0000"),
            currency="IDR",
            available_stock=100,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(variant)
        await session.commit()

        found = await get_variant_by_id(session, variant.id)
        assert found is not None
        assert found.variant_sku == "VAR-001"

    @pytest.mark.asyncio
    async def test_list_active_products(self, session: AsyncSession):
        from app.repositories.products import list_active_products
        from app.db.models import Product

        for i in range(3):
            session.add(Product(
                id=_uuid(),
                sku=f"SKU-ACTIVE-{i}",
                name=f"Active Product {i}",
                description="Active",
                is_active=True,
                created_at=_now(),
                updated_at=_now(),
            ))
        session.add(Product(
            id=_uuid(),
            sku="SKU-INACTIVE",
            name="Inactive Product",
            description="Inactive",
            is_active=False,
            created_at=_now(),
            updated_at=_now(),
        ))
        await session.commit()

        products = await list_active_products(session)
        assert len(products) == 3
        assert all(p.is_active for p in products)


# ---------------------------------------------------------------------------
# Tests: carts
# ---------------------------------------------------------------------------


class TestCartsRepo:
    async def _create_customer(self, session: AsyncSession) -> uuid.UUID:
        from app.repositories.customers import upsert
        customer = await upsert(session, f"+628{uuid.uuid4().hex[:10]}")
        await session.commit()
        return customer.id

    async def _create_product_variant(self, session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        from app.db.models import Product, ProductVariant
        product = Product(
            id=_uuid(), sku=f"SKU-{_uuid().hex[:6]}", name="P", description="D",
            is_active=True, created_at=_now(), updated_at=_now(),
        )
        session.add(product)
        await session.flush()
        variant = ProductVariant(
            id=_uuid(), product_id=product.id, variant_sku=f"V-{_uuid().hex[:6]}",
            attributes={}, current_price=Decimal("10000"), currency="IDR",
            available_stock=50, created_at=_now(), updated_at=_now(),
        )
        session.add(variant)
        await session.commit()
        return product.id, variant.id

    @pytest.mark.asyncio
    async def test_create_and_get_active_cart(self, session: AsyncSession):
        from app.repositories.carts import create_cart, get_active_cart

        customer_id = await self._create_customer(session)
        cart = await create_cart(session, customer_id=customer_id, currency="IDR")
        await session.commit()

        assert cart.status == "OPEN"
        assert cart.subtotal == Decimal("0")

        found = await get_active_cart(session, customer_id)
        assert found is not None
        assert found.id == cart.id

    @pytest.mark.asyncio
    async def test_add_and_get_cart_items(self, session: AsyncSession):
        from app.repositories.carts import create_cart, add_cart_item, get_cart_items

        customer_id = await self._create_customer(session)
        product_id, variant_id = await self._create_product_variant(session)

        cart = await create_cart(session, customer_id=customer_id, currency="IDR")
        await session.commit()

        item = await add_cart_item(
            session,
            cart_id=cart.id,
            product_id=product_id,
            variant_id=variant_id,
            quantity=2,
            unit_price_snapshot=Decimal("10000"),
            currency="IDR",
        )
        await session.commit()

        assert item is not None
        assert item.quantity == 2

        items = await get_cart_items(session, cart.id)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_update_cart_item_quantity(self, session: AsyncSession):
        from app.repositories.carts import (
            create_cart, add_cart_item, update_cart_item_quantity, get_cart_items,
        )

        customer_id = await self._create_customer(session)
        product_id, variant_id = await self._create_product_variant(session)

        cart = await create_cart(session, customer_id=customer_id, currency="IDR")
        await session.commit()

        item = await add_cart_item(
            session, cart_id=cart.id, product_id=product_id, variant_id=variant_id,
            quantity=2, unit_price_snapshot=Decimal("10000"), currency="IDR",
        )
        await session.commit()

        await update_cart_item_quantity(session, item.id, 5)
        await session.commit()

        items = await get_cart_items(session, cart.id)
        assert items[0].quantity == 5

    @pytest.mark.asyncio
    async def test_remove_cart_item(self, session: AsyncSession):
        from app.repositories.carts import (
            create_cart, add_cart_item, remove_cart_item, get_cart_items,
        )

        customer_id = await self._create_customer(session)
        product_id, variant_id = await self._create_product_variant(session)

        cart = await create_cart(session, customer_id=customer_id, currency="IDR")
        await session.commit()

        item = await add_cart_item(
            session, cart_id=cart.id, product_id=product_id, variant_id=variant_id,
            quantity=1, unit_price_snapshot=Decimal("10000"), currency="IDR",
        )
        await session.commit()

        await remove_cart_item(session, item.id)
        await session.commit()

        items = await get_cart_items(session, cart.id)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_update_cart_status(self, session: AsyncSession):
        from app.repositories.carts import create_cart, update_cart_status, get_cart_by_id

        customer_id = await self._create_customer(session)
        cart = await create_cart(session, customer_id=customer_id, currency="IDR")
        await session.commit()

        await update_cart_status(session, cart.id, "CONVERTED")
        await session.commit()

        found = await get_cart_by_id(session, cart.id)
        assert found is not None
        assert found.status == "CONVERTED"


# ---------------------------------------------------------------------------
# Tests: orders
# ---------------------------------------------------------------------------


class TestOrdersRepo:
    async def _setup_order_prereqs(self, session: AsyncSession):
        from app.repositories.customers import upsert
        from app.repositories.carts import create_cart

        customer = await upsert(session, f"+628{uuid.uuid4().hex[:10]}")
        await session.commit()
        cart = await create_cart(session, customer_id=customer.id, currency="IDR")
        await session.commit()
        return customer.id, cart.id

    @pytest.mark.asyncio
    async def test_create_and_get_order(self, session: AsyncSession):
        from app.repositories.orders import create_order, get_order_by_id

        customer_id, cart_id = await self._setup_order_prereqs(session)

        order = await create_order(
            session,
            customer_id=customer_id,
            cart_id=cart_id,
            total=Decimal("100000"),
            currency="IDR",
        )
        await session.commit()

        assert order.status == "pending_payment"
        assert order.total == Decimal("100000")

        found = await get_order_by_id(session, order.id)
        assert found is not None
        assert found.id == order.id

    @pytest.mark.asyncio
    async def test_update_order_status(self, session: AsyncSession):
        from app.repositories.orders import create_order, update_order_status, get_order_by_id

        customer_id, cart_id = await self._setup_order_prereqs(session)

        order = await create_order(
            session, customer_id=customer_id, cart_id=cart_id,
            total=Decimal("100000"), currency="IDR",
        )
        await session.commit()

        updated = await update_order_status(session, order.id, "paid", paid_at=_now())
        await session.commit()
        assert updated is True

        found = await get_order_by_id(session, order.id)
        assert found is not None
        assert found.status == "paid"
        assert found.paid_at is not None

    @pytest.mark.asyncio
    async def test_create_order_item(self, session: AsyncSession):
        from app.repositories.orders import create_order, create_order_item, get_order_items
        from app.db.models import Product, ProductVariant

        customer_id, cart_id = await self._setup_order_prereqs(session)

        product = Product(
            id=_uuid(), sku=f"SKU-{_uuid().hex[:6]}", name="P", description="D",
            is_active=True, created_at=_now(), updated_at=_now(),
        )
        session.add(product)
        await session.flush()
        variant = ProductVariant(
            id=_uuid(), product_id=product.id, variant_sku=f"V-{_uuid().hex[:6]}",
            attributes={}, current_price=Decimal("50000"), currency="IDR",
            available_stock=10, created_at=_now(), updated_at=_now(),
        )
        session.add(variant)
        await session.commit()

        order = await create_order(
            session, customer_id=customer_id, cart_id=cart_id,
            total=Decimal("100000"), currency="IDR",
        )
        await session.commit()

        item = await create_order_item(
            session,
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            quantity=2,
            unit_price_snapshot=Decimal("50000"),
            currency="IDR",
            line_total=Decimal("100000"),
        )
        await session.commit()

        assert item is not None
        items = await get_order_items(session, order.id)
        assert len(items) == 1
        assert items[0].quantity == 2


# ---------------------------------------------------------------------------
# Tests: payments
# ---------------------------------------------------------------------------


class TestPaymentsRepo:
    async def _setup_payment_prereqs(self, session: AsyncSession) -> uuid.UUID:
        from app.repositories.customers import upsert
        from app.repositories.carts import create_cart
        from app.repositories.orders import create_order

        customer = await upsert(session, f"+628{uuid.uuid4().hex[:10]}")
        await session.commit()
        cart = await create_cart(session, customer_id=customer.id, currency="IDR")
        await session.commit()
        order = await create_order(
            session, customer_id=customer.id, cart_id=cart.id,
            total=Decimal("100000"), currency="IDR",
        )
        await session.commit()
        return order.id

    @pytest.mark.asyncio
    async def test_create_and_get_payment(self, session: AsyncSession):
        from app.repositories.payments import create_payment, get_payment_by_id

        order_id = await self._setup_payment_prereqs(session)

        payment = await create_payment(
            session,
            order_id=order_id,
            provider="xendit",
            provider_ref="xnd_ref_001",
            link_url="https://pay.example.com/abc",
            amount=Decimal("100000"),
            currency="IDR",
            expires_at=_now() + timedelta(hours=24),
        )
        await session.commit()

        assert payment.status == "created"

        found = await get_payment_by_id(session, payment.id)
        assert found is not None
        assert found.provider_ref == "xnd_ref_001"

    @pytest.mark.asyncio
    async def test_update_payment_status(self, session: AsyncSession):
        from app.repositories.payments import create_payment, update_payment_status, get_payment_by_id

        order_id = await self._setup_payment_prereqs(session)

        payment = await create_payment(
            session, order_id=order_id, provider="xendit",
            provider_ref="xnd_ref_002", link_url="https://pay.example.com/def",
            amount=Decimal("100000"), currency="IDR",
            expires_at=_now() + timedelta(hours=24),
        )
        await session.commit()

        updated = await update_payment_status(
            session, payment.id, "paid", paid_at=_now(),
            verification_metadata={"event_id": "evt_123"},
        )
        await session.commit()
        assert updated is True

        found = await get_payment_by_id(session, payment.id)
        assert found is not None
        assert found.status == "paid"
        assert found.paid_at is not None

    @pytest.mark.asyncio
    async def test_get_payments_by_order(self, session: AsyncSession):
        from app.repositories.payments import create_payment, get_payments_by_order

        order_id = await self._setup_payment_prereqs(session)

        await create_payment(
            session, order_id=order_id, provider="xendit",
            provider_ref="xnd_ref_003", link_url="https://pay.example.com/ghi",
            amount=Decimal("100000"), currency="IDR",
            expires_at=_now() + timedelta(hours=24),
        )
        await session.commit()

        payments = await get_payments_by_order(session, order_id)
        assert len(payments) == 1


# ---------------------------------------------------------------------------
# Tests: webhook_events
# ---------------------------------------------------------------------------


class TestWebhookEventsRepo:
    @pytest.mark.asyncio
    async def test_try_insert_first_time(self, session: AsyncSession):
        from app.repositories.webhook_events import try_insert

        event = await try_insert(
            session,
            webhook_event_id="evt_001",
            payment_id=None,
            event_kind="paid",
            raw_payload={"amount": 100000},
        )
        await session.commit()

        assert event is not None
        assert event.webhook_event_id == "evt_001"

    @pytest.mark.asyncio
    async def test_try_insert_duplicate_returns_none(self, session: AsyncSession):
        from app.repositories.webhook_events import try_insert

        await try_insert(
            session,
            webhook_event_id="evt_001",
            payment_id=None,
            event_kind="paid",
            raw_payload={"amount": 100000},
        )
        await session.commit()

        # Duplicate
        result = await try_insert(
            session,
            webhook_event_id="evt_001",
            payment_id=None,
            event_kind="paid",
            raw_payload={"amount": 200000},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_webhook_event_id(self, session: AsyncSession):
        from app.repositories.webhook_events import try_insert, get_by_webhook_event_id

        await try_insert(
            session,
            webhook_event_id="evt_002",
            payment_id=None,
            event_kind="failed",
            raw_payload={},
        )
        await session.commit()

        found = await get_by_webhook_event_id(session, "evt_002")
        assert found is not None
        assert found.event_kind == "failed"

        not_found = await get_by_webhook_event_id(session, "nonexistent")
        assert not_found is None


# ---------------------------------------------------------------------------
# Tests: dispatched_actions
# ---------------------------------------------------------------------------


class TestDispatchedActionsRepo:
    async def _setup_order(self, session: AsyncSession) -> uuid.UUID:
        from app.repositories.customers import upsert
        from app.repositories.carts import create_cart
        from app.repositories.orders import create_order

        customer = await upsert(session, f"+628{uuid.uuid4().hex[:10]}")
        await session.commit()
        cart = await create_cart(session, customer_id=customer.id, currency="IDR")
        await session.commit()
        order = await create_order(
            session, customer_id=customer.id, cart_id=cart.id,
            total=Decimal("100000"), currency="IDR",
        )
        await session.commit()
        return order.id

    @pytest.mark.asyncio
    async def test_try_dispatch_first_time(self, session: AsyncSession):
        from app.repositories.dispatched_actions import try_dispatch

        order_id = await self._setup_order(session)

        action = await try_dispatch(
            session, order_id=order_id, action_type="logistics_prepare"
        )
        await session.commit()

        assert action is not None
        assert action.action_type == "logistics_prepare"

    @pytest.mark.asyncio
    async def test_try_dispatch_duplicate_returns_none(self, session: AsyncSession):
        from app.repositories.dispatched_actions import try_dispatch

        order_id = await self._setup_order(session)

        await try_dispatch(session, order_id=order_id, action_type="logistics_prepare")
        await session.commit()

        # Duplicate
        result = await try_dispatch(
            session, order_id=order_id, action_type="logistics_prepare"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_different_action_types_allowed(self, session: AsyncSession):
        from app.repositories.dispatched_actions import try_dispatch, get_dispatched_actions_for_order

        order_id = await self._setup_order(session)

        await try_dispatch(session, order_id=order_id, action_type="logistics_prepare")
        await session.commit()
        await try_dispatch(session, order_id=order_id, action_type="payment_confirmation_reply")
        await session.commit()

        actions = await get_dispatched_actions_for_order(session, order_id)
        assert len(actions) == 2


# ---------------------------------------------------------------------------
# Tests: shipments
# ---------------------------------------------------------------------------


class TestShipmentsRepo:
    async def _setup_order(self, session: AsyncSession) -> uuid.UUID:
        from app.repositories.customers import upsert
        from app.repositories.carts import create_cart
        from app.repositories.orders import create_order

        customer = await upsert(session, f"+628{uuid.uuid4().hex[:10]}")
        await session.commit()
        cart = await create_cart(session, customer_id=customer.id, currency="IDR")
        await session.commit()
        order = await create_order(
            session, customer_id=customer.id, cart_id=cart.id,
            total=Decimal("100000"), currency="IDR",
        )
        await session.commit()
        return order.id

    @pytest.mark.asyncio
    async def test_create_and_get_shipment(self, session: AsyncSession):
        from app.repositories.shipments import create_shipment, get_shipment_by_order

        order_id = await self._setup_order(session)

        shipment = await create_shipment(
            session, order_id=order_id, tracking_number="TRACK12345678"
        )
        await session.commit()

        assert shipment.status == "prepared"
        assert shipment.tracking_number == "TRACK12345678"

        found = await get_shipment_by_order(session, order_id)
        assert found is not None
        assert found.id == shipment.id

    @pytest.mark.asyncio
    async def test_get_shipment_by_tracking(self, session: AsyncSession):
        from app.repositories.shipments import create_shipment, get_shipment_by_tracking

        order_id = await self._setup_order(session)

        await create_shipment(
            session, order_id=order_id, tracking_number="TRACK99999999"
        )
        await session.commit()

        found = await get_shipment_by_tracking(session, "TRACK99999999")
        assert found is not None

        not_found = await get_shipment_by_tracking(session, "NONEXISTENT")
        assert not_found is None

    @pytest.mark.asyncio
    async def test_update_shipment_status(self, session: AsyncSession):
        from app.repositories.shipments import create_shipment, update_shipment_status, get_shipment_by_order

        order_id = await self._setup_order(session)

        shipment = await create_shipment(
            session, order_id=order_id, tracking_number="TRACK11111111"
        )
        await session.commit()

        updated = await update_shipment_status(session, shipment.id, "in_transit")
        await session.commit()
        assert updated is True

        found = await get_shipment_by_order(session, order_id)
        assert found is not None
        assert found.status == "in_transit"


# ---------------------------------------------------------------------------
# Tests: audit
# ---------------------------------------------------------------------------


class TestAuditRepo:
    @pytest.mark.asyncio
    async def test_insert_audit_log(self, session: AsyncSession):
        from app.repositories.audit import insert_audit_log

        log = await insert_audit_log(
            session,
            actor="system",
            event_type="tool_invocation",
            tool_name="search_products",
            request_id="req_001",
        )
        await session.commit()

        assert log is not None
        assert log.actor == "system"
        assert log.event_type == "tool_invocation"

    @pytest.mark.asyncio
    async def test_insert_audit_log_failure(self, session: AsyncSession):
        from app.repositories.audit import insert_audit_log_failure

        failure = await insert_audit_log_failure(
            session,
            actor="system",
            event_type="tool_invocation",
            tool_name="create_order",
            attempt_count=3,
            last_error="connection timeout",
        )
        await session.commit()

        assert failure is not None
        assert failure.attempt_count == 3
        assert failure.last_error == "connection timeout"

    @pytest.mark.asyncio
    async def test_get_pending_failures(self, session: AsyncSession):
        from app.repositories.audit import insert_audit_log_failure, get_pending_failures

        await insert_audit_log_failure(
            session, actor="system", event_type="flush_failed",
            attempt_count=1, last_error="db timeout",
        )
        await session.commit()

        failures = await get_pending_failures(session)
        assert len(failures) == 1


# ---------------------------------------------------------------------------
# Tests: escalations
# ---------------------------------------------------------------------------


class TestEscalationsRepo:
    async def _setup_conversation(self, session: AsyncSession) -> uuid.UUID:
        from app.repositories.customers import upsert
        from app.repositories.conversations import get_or_create_by_customer

        customer = await upsert(session, f"+628{uuid.uuid4().hex[:10]}")
        await session.commit()
        conv = await get_or_create_by_customer(session, customer.id)
        await session.commit()
        return conv.id

    @pytest.mark.asyncio
    async def test_create_and_get_active_escalation(self, session: AsyncSession):
        from app.repositories.escalations import create_escalation, get_active_escalation

        conv_id = await self._setup_conversation(session)

        escalation = await create_escalation(
            session,
            conversation_id=conv_id,
            reason="customer_request",
            actor="agent",
        )
        await session.commit()

        assert escalation.is_active is True

        found = await get_active_escalation(session, conv_id)
        assert found is not None
        assert found.id == escalation.id

    @pytest.mark.asyncio
    async def test_clear_escalation(self, session: AsyncSession):
        from app.repositories.escalations import (
            create_escalation, clear_escalation, get_active_escalation,
        )

        conv_id = await self._setup_conversation(session)

        escalation = await create_escalation(
            session, conversation_id=conv_id, reason="low_confidence", actor="agent",
        )
        await session.commit()

        cleared = await clear_escalation(session, escalation.id)
        await session.commit()
        assert cleared is True

        found = await get_active_escalation(session, conv_id)
        assert found is None

    @pytest.mark.asyncio
    async def test_clear_all_for_conversation(self, session: AsyncSession):
        from app.repositories.escalations import (
            create_escalation, clear_all_for_conversation, get_active_escalation,
        )

        conv_id = await self._setup_conversation(session)

        await create_escalation(
            session, conversation_id=conv_id, reason="reason1", actor="agent",
        )
        await session.commit()
        await create_escalation(
            session, conversation_id=conv_id, reason="reason2", actor="system",
        )
        await session.commit()

        count = await clear_all_for_conversation(session, conv_id)
        await session.commit()
        assert count == 2

        found = await get_active_escalation(session, conv_id)
        assert found is None

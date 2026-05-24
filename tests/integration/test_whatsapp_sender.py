"""Integration tests for the WhatsApp sender worker.

Uses an in-memory SQLite database and httpx mock transport to stub the
WhatsApp Gateway. Verifies:
  (a) A single successful send results in status='sent'
  (b) A server returning 500 three times results in status='failed',
      attempts=3, and an audit row with event_type='outbound_send_failure'
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AuditLog, Base, MessageOutbound
from app.observability.audit_logger import AuditLogger
from app.schemas.whatsapp import WhatsAppSendTask
from app.workers.queue import AsyncioWorkerQueue
from app.workers.whatsapp_sender import WhatsAppSenderWorker

GATEWAY_URL = "http://fake-gateway:3000"
GATEWAY_TOKEN = "test-token-123"


@pytest_asyncio.fixture
async def db_session_factory():
    """Create an in-memory SQLite async engine and session factory for testing."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
def audit_logger(db_session_factory: async_sessionmaker[AsyncSession]) -> AuditLogger:
    """Create an AuditLogger backed by the test database."""
    return AuditLogger(session_factory=db_session_factory)


@pytest.fixture
def send_queue() -> AsyncioWorkerQueue[WhatsAppSendTask]:
    """Create a fresh send queue."""
    return AsyncioWorkerQueue[WhatsAppSendTask]()


@pytest_asyncio.fixture
async def seed_outbound_message(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> MessageOutbound:
    """Seed a 'queued' outbound message and return it."""
    msg = MessageOutbound(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        to_phone_e164="+6281234567890",
        text_body="Hello, your order is confirmed!",
        status="queued",
        attempts=0,
        created_at=datetime.now(timezone.utc),
    )
    async with db_session_factory() as session:
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
    return msg


def _make_success_transport(gateway_message_id: str) -> httpx.MockTransport:
    """Create a mock transport that always returns 200 with a message_id."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "sent", "message_id": gateway_message_id},
        )

    return httpx.MockTransport(handler)


def _make_failure_transport() -> httpx.MockTransport:
    """Create a mock transport that always returns 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": "Internal Server Error"},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_successful_send_updates_status_to_sent(
    db_session_factory: async_sessionmaker[AsyncSession],
    audit_logger: AuditLogger,
    send_queue: AsyncioWorkerQueue[WhatsAppSendTask],
    seed_outbound_message: MessageOutbound,
) -> None:
    """Test (a): A single successful send returns status='sent'."""
    msg = seed_outbound_message
    gateway_message_id = f"wamid.{uuid.uuid4()}"

    transport = _make_success_transport(gateway_message_id)
    http_client = httpx.AsyncClient(transport=transport)

    worker = WhatsAppSenderWorker(
        queue=send_queue,
        session_factory=db_session_factory,
        audit_logger=audit_logger,
        gateway_url=GATEWAY_URL,
        gateway_token=GATEWAY_TOKEN,
        http_client=http_client,
    )

    # Enqueue the task
    task = WhatsAppSendTask(
        outbound_message_id=msg.id,
        conversation_id=msg.conversation_id,
        to_phone_e164=msg.to_phone_e164,
        text_body=msg.text_body,
    )
    await send_queue.put(task)

    # Run the worker for one task
    worker_task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.5)
    worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    await http_client.aclose()

    # Verify the outbound message status
    async with db_session_factory() as session:
        stmt = select(MessageOutbound).where(MessageOutbound.id == msg.id)
        result = await session.execute(stmt)
        updated_msg = result.scalar_one()

        assert updated_msg.status == "sent"
        assert updated_msg.whatsapp_message_id == gateway_message_id
        assert updated_msg.attempts == 1
        assert updated_msg.sent_at is not None


@pytest.mark.asyncio
async def test_three_failures_results_in_failed_status_and_audit(
    db_session_factory: async_sessionmaker[AsyncSession],
    audit_logger: AuditLogger,
    send_queue: AsyncioWorkerQueue[WhatsAppSendTask],
    seed_outbound_message: MessageOutbound,
) -> None:
    """Test (b): Server returning 500 three times -> status='failed', attempts=3, audit row."""
    msg = seed_outbound_message

    transport = _make_failure_transport()
    http_client = httpx.AsyncClient(transport=transport)

    worker = WhatsAppSenderWorker(
        queue=send_queue,
        session_factory=db_session_factory,
        audit_logger=audit_logger,
        gateway_url=GATEWAY_URL,
        gateway_token=GATEWAY_TOKEN,
        http_client=http_client,
    )

    # Enqueue the task
    task = WhatsAppSendTask(
        outbound_message_id=msg.id,
        conversation_id=msg.conversation_id,
        to_phone_e164=msg.to_phone_e164,
        text_body=msg.text_body,
    )
    await send_queue.put(task)

    # Run the worker - backoff delays are 1s, 2s so total ~3s + processing
    worker_task = asyncio.create_task(worker.start())
    # Wait enough time for 3 attempts with backoff (1+2 = 3s) + buffer
    await asyncio.sleep(5.0)
    worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    await http_client.aclose()

    # Verify the outbound message status
    async with db_session_factory() as session:
        stmt = select(MessageOutbound).where(MessageOutbound.id == msg.id)
        result = await session.execute(stmt)
        updated_msg = result.scalar_one()

        assert updated_msg.status == "failed"
        assert updated_msg.attempts == 3
        assert updated_msg.last_error is not None
        assert "500" in updated_msg.last_error

    # Verify audit log row exists
    async with db_session_factory() as session:
        stmt = select(AuditLog).where(AuditLog.event_type == "outbound_send_failure")
        result = await session.execute(stmt)
        audit_row = result.scalar_one_or_none()

        assert audit_row is not None
        assert audit_row.actor == "whatsapp_sender_worker"
        assert audit_row.conversation_id == msg.conversation_id

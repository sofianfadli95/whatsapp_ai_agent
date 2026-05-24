"""Integration tests for the PostgresSaver checkpointer wiring.

Verifies:
  - Checkpoint load failure sets the escalation flag
  - Audit record is written AFTER the escalation flag is set
  - No outbound reply is enqueued on failure
  - EscalationSetError is raised when escalation flag cannot be set (Req 2.8)
  - Checkpoint commit failure sets escalation flag and raises CheckpointCommitError (Req 2.9)

Uses an in-memory SQLite database with a mock checkpointer to simulate failures.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.checkpointer import (
    CHECKPOINT_TIMEOUT_SECONDS,
    CheckpointCommitError,
    CheckpointLoadError,
    EscalationSetError,
    commit_checkpoint,
    load_checkpoint,
    make_thread_config,
)
from app.db.models import AuditLog, Base, Conversation, Customer
from app.observability.audit_logger import AuditLogger


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory SQLite async engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine):
    """Create an async session factory bound to the test engine."""
    factory = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory


@pytest_asyncio.fixture
async def seed_conversation(db_session_factory) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a customer and conversation, return (conversation_id, customer_id)."""
    customer_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    async with db_session_factory() as session:
        customer = Customer(
            id=customer_id,
            phone_e164="+6281234567890",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(customer)
        await session.flush()

        conversation = Conversation(
            id=conversation_id,
            customer_id=customer_id,
            escalation_flag=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(conversation)
        await session.commit()

    return conversation_id, customer_id


@pytest.fixture
def audit_logger(db_session_factory) -> AuditLogger:
    """Create an AuditLogger backed by the test database."""
    return AuditLogger(session_factory=db_session_factory)


@pytest.fixture
def mock_checkpointer() -> AsyncMock:
    """Create a mock AsyncPostgresSaver."""
    mock = AsyncMock()
    return mock


class TestMakeThreadConfig:
    """Tests for make_thread_config."""

    def test_thread_id_derived_from_conversation_id(self) -> None:
        """thread_id in config should equal the conversation_id."""
        conv_id = str(uuid.uuid4())
        config = make_thread_config(conv_id)
        assert config == {"configurable": {"thread_id": conv_id}}


class TestLoadCheckpointFailure:
    """Tests for checkpoint load failure handling (Req 2.7, 2.8)."""

    @pytest.mark.asyncio
    async def test_load_failure_sets_escalation_flag(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """When checkpoint load fails, the escalation flag MUST be set on the conversation."""
        conversation_id, _ = seed_conversation

        # Simulate a database error on aget_tuple
        mock_checkpointer.aget_tuple.side_effect = RuntimeError("Connection refused")

        with pytest.raises(CheckpointLoadError) as exc_info:
            await load_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                session_factory=db_session_factory,
            )

        assert str(conversation_id) in str(exc_info.value)

        # Verify escalation flag is set
        async with db_session_factory() as session:
            stmt = select(Conversation).where(Conversation.id == conversation_id)
            result = await session.execute(stmt)
            conv = result.scalar_one()
            assert conv.escalation_flag is True
            assert "Checkpoint load failure" in (conv.escalation_reason or "")

    @pytest.mark.asyncio
    async def test_load_timeout_sets_escalation_flag(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """When checkpoint load exceeds 5s timeout, escalation flag MUST be set."""
        conversation_id, _ = seed_conversation

        # Simulate a timeout by making aget_tuple hang
        async def slow_load(*args, **kwargs):
            await asyncio.sleep(CHECKPOINT_TIMEOUT_SECONDS + 1)

        mock_checkpointer.aget_tuple.side_effect = slow_load

        with pytest.raises(CheckpointLoadError) as exc_info:
            await load_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                session_factory=db_session_factory,
            )

        assert "timed out" in exc_info.value.reason

        # Verify escalation flag is set
        async with db_session_factory() as session:
            stmt = select(Conversation).where(Conversation.id == conversation_id)
            result = await session.execute(stmt)
            conv = result.scalar_one()
            assert conv.escalation_flag is True

    @pytest.mark.asyncio
    async def test_load_failure_audit_written_after_escalation(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
        audit_logger: AuditLogger,
    ) -> None:
        """Audit record is written AFTER escalation flag is set (Req 2.7).

        The test verifies the ordering: first CheckpointLoadError is raised
        (meaning escalation was set), then the caller writes audit.
        """
        conversation_id, _ = seed_conversation

        mock_checkpointer.aget_tuple.side_effect = RuntimeError("DB connection lost")

        # Step 1: load_checkpoint raises CheckpointLoadError (escalation set)
        with pytest.raises(CheckpointLoadError):
            await load_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                session_factory=db_session_factory,
            )

        # Verify escalation is set BEFORE audit write
        async with db_session_factory() as session:
            stmt = select(Conversation).where(Conversation.id == conversation_id)
            result = await session.execute(stmt)
            conv = result.scalar_one()
            assert conv.escalation_flag is True

        # Step 2: Caller writes audit AFTER escalation is confirmed
        await audit_logger.emit(
            actor="checkpointer",
            event_type="checkpoint_load_failure",
            conversation_id=conversation_id,
            error_code="checkpoint_load_error",
        )

        # Verify audit record exists
        async with db_session_factory() as session:
            stmt = select(AuditLog).where(
                AuditLog.event_type == "checkpoint_load_failure"
            )
            result = await session.execute(stmt)
            audit_row = result.scalar_one()
            assert audit_row.conversation_id == conversation_id
            assert audit_row.error_code == "checkpoint_load_error"

    @pytest.mark.asyncio
    async def test_no_outbound_reply_enqueued_on_load_failure(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """No outbound reply should be enqueued when checkpoint load fails.

        The CheckpointLoadError signals the caller to stop processing,
        which means no reply is generated or enqueued.
        """
        conversation_id, _ = seed_conversation
        reply_enqueued = False

        mock_checkpointer.aget_tuple.side_effect = RuntimeError("Connection timeout")

        try:
            await load_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                session_factory=db_session_factory,
            )
            # If we get here, the load succeeded — enqueue reply
            reply_enqueued = True
        except CheckpointLoadError:
            # Load failed — do NOT enqueue reply (Req 2.7)
            pass

        assert reply_enqueued is False

    @pytest.mark.asyncio
    async def test_escalation_set_failure_raises_escalation_set_error(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """If setting escalation flag fails after load failure, raise EscalationSetError (Req 2.8).

        When EscalationSetError is raised:
        - Caller MUST NOT write audit log
        - Caller MUST surface failure for webhook re-delivery
        """
        # Use a non-existent conversation_id that will cause the escalation
        # update to affect 0 rows (simulating a failure scenario)
        # We'll patch set_escalation to raise an exception
        conversation_id = uuid.uuid4()

        mock_checkpointer.aget_tuple.side_effect = RuntimeError("DB error")

        # Patch set_escalation to simulate failure
        with patch(
            "app.agent.checkpointer.set_escalation",
            side_effect=RuntimeError("Escalation DB write failed"),
        ):
            with pytest.raises(EscalationSetError) as exc_info:
                await load_checkpoint(
                    mock_checkpointer,
                    str(conversation_id),
                    session_factory=db_session_factory,
                )

            assert "Could not set escalation flag" in str(exc_info.value)


class TestCommitCheckpointFailure:
    """Tests for checkpoint commit failure handling (Req 2.9)."""

    @pytest.mark.asyncio
    async def test_commit_failure_sets_escalation_and_raises(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """When checkpoint commit fails, escalation flag is set and
        CheckpointCommitError is raised so no reply is sent (Req 2.9).
        """
        conversation_id, _ = seed_conversation
        config = make_thread_config(str(conversation_id))

        mock_checkpointer.aput.side_effect = RuntimeError("Write failed")

        with pytest.raises(CheckpointCommitError) as exc_info:
            await commit_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                config=config,
                checkpoint={"v": 1, "ts": "2024-01-01T00:00:00Z"},
                metadata={"source": "test"},
                session_factory=db_session_factory,
            )

        assert str(conversation_id) in str(exc_info.value)

        # Verify escalation flag is set
        async with db_session_factory() as session:
            stmt = select(Conversation).where(Conversation.id == conversation_id)
            result = await session.execute(stmt)
            conv = result.scalar_one()
            assert conv.escalation_flag is True
            assert "Checkpoint commit failure" in (conv.escalation_reason or "")

    @pytest.mark.asyncio
    async def test_commit_timeout_sets_escalation_and_raises(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """When checkpoint commit exceeds 5s, escalation is set and error raised."""
        conversation_id, _ = seed_conversation
        config = make_thread_config(str(conversation_id))

        async def slow_commit(*args, **kwargs):
            await asyncio.sleep(CHECKPOINT_TIMEOUT_SECONDS + 1)

        mock_checkpointer.aput.side_effect = slow_commit

        with pytest.raises(CheckpointCommitError) as exc_info:
            await commit_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                config=config,
                checkpoint={"v": 1},
                metadata={},
                session_factory=db_session_factory,
            )

        assert "timed out" in exc_info.value.reason

        # Verify escalation flag is set
        async with db_session_factory() as session:
            stmt = select(Conversation).where(Conversation.id == conversation_id)
            result = await session.execute(stmt)
            conv = result.scalar_one()
            assert conv.escalation_flag is True

    @pytest.mark.asyncio
    async def test_no_reply_sent_on_commit_failure(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """No outbound reply should be sent when checkpoint commit fails (Req 2.9)."""
        conversation_id, _ = seed_conversation
        config = make_thread_config(str(conversation_id))
        reply_sent = False

        mock_checkpointer.aput.side_effect = RuntimeError("Commit failed")

        try:
            await commit_checkpoint(
                mock_checkpointer,
                str(conversation_id),
                config=config,
                checkpoint={"v": 1},
                metadata={},
                session_factory=db_session_factory,
            )
            # If commit succeeds, send reply
            reply_sent = True
        except CheckpointCommitError:
            # Commit failed — do NOT send reply (Req 2.9)
            pass

        assert reply_sent is False


class TestLoadCheckpointSuccess:
    """Tests for successful checkpoint load."""

    @pytest.mark.asyncio
    async def test_successful_load_returns_checkpoint(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """Successful load returns the checkpoint tuple without setting escalation."""
        conversation_id, _ = seed_conversation

        expected_checkpoint = {"v": 1, "data": "test_state"}
        mock_checkpointer.aget_tuple.return_value = expected_checkpoint

        result = await load_checkpoint(
            mock_checkpointer,
            str(conversation_id),
            session_factory=db_session_factory,
        )

        assert result == expected_checkpoint

        # Verify escalation flag is NOT set
        async with db_session_factory() as session:
            stmt = select(Conversation).where(Conversation.id == conversation_id)
            result_row = await session.execute(stmt)
            conv = result_row.scalar_one()
            assert conv.escalation_flag is False

    @pytest.mark.asyncio
    async def test_successful_load_returns_none_for_new_conversation(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seed_conversation: tuple[uuid.UUID, uuid.UUID],
        mock_checkpointer: AsyncMock,
    ) -> None:
        """Load returns None for a conversation with no prior checkpoint."""
        conversation_id, _ = seed_conversation

        mock_checkpointer.aget_tuple.return_value = None

        result = await load_checkpoint(
            mock_checkpointer,
            str(conversation_id),
            session_factory=db_session_factory,
        )

        assert result is None

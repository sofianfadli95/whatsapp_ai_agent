"""PostgresSaver checkpointer wiring for LangGraph.

Wraps LangGraph's AsyncPostgresSaver with 5-second timeouts on load and commit
operations. On failure, sets the escalation flag transactionally before any
audit write, and surfaces a non-success response to the inbound handler.

Requirements: Req 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10
Design: Components and Interfaces — Agent Layer, Properties 5, 6
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.conversations import set_escalation

logger = structlog.stdlib.get_logger(__name__)

# Timeout for checkpoint load/commit operations (seconds)
CHECKPOINT_TIMEOUT_SECONDS: float = 5.0


class CheckpointLoadError(Exception):
    """Raised when loading conversation state from the checkpointer fails.

    This includes database errors, connection errors, or operations exceeding
    the 5-second timeout (Req 2.7).
    """

    def __init__(self, conversation_id: str, reason: str) -> None:
        self.conversation_id = conversation_id
        self.reason = reason
        super().__init__(
            f"Checkpoint load failed for conversation {conversation_id}: {reason}"
        )


class CheckpointCommitError(Exception):
    """Raised when committing conversation state to the checkpointer fails.

    The caller MUST NOT send the outbound reply when this is raised (Req 2.9).
    """

    def __init__(self, conversation_id: str, reason: str) -> None:
        self.conversation_id = conversation_id
        self.reason = reason
        super().__init__(
            f"Checkpoint commit failed for conversation {conversation_id}: {reason}"
        )


class EscalationSetError(Exception):
    """Raised when setting the escalation flag fails after a checkpoint failure.

    When this is raised, the caller MUST NOT write an audit log entry and
    MUST surface the failure so the webhook is re-delivered (Req 2.8).
    """

    def __init__(self, conversation_id: str, reason: str) -> None:
        self.conversation_id = conversation_id
        self.reason = reason
        super().__init__(
            f"Failed to set escalation flag for conversation {conversation_id}: {reason}"
        )


def build_checkpointer(connection_string: str) -> AsyncPostgresSaver:
    """Build an AsyncPostgresSaver using the provided connection string.

    The checkpointer uses its own async connection pool managed by
    psycopg (the underlying driver for langgraph-checkpoint-postgres).

    Args:
        connection_string: PostgreSQL connection string. Should use the
            psycopg driver format (e.g., postgresql+psycopg://... or
            postgresql://...).

    Returns:
        An AsyncPostgresSaver instance ready for use with LangGraph.
    """
    # AsyncPostgresSaver.from_conn_string creates its own connection pool
    # Convert SQLAlchemy-style URL to psycopg-compatible format if needed
    conn_str = connection_string
    if conn_str.startswith("postgresql+asyncpg://"):
        conn_str = conn_str.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif conn_str.startswith("postgresql+psycopg://"):
        conn_str = conn_str.replace("postgresql+psycopg://", "postgresql://", 1)

    checkpointer = AsyncPostgresSaver.from_conn_string(conn_str)
    return checkpointer


def make_thread_config(conversation_id: str) -> dict[str, Any]:
    """Create a LangGraph config dict with thread_id derived from conversation_id.

    Args:
        conversation_id: The conversation UUID string used as the thread_id.

    Returns:
        A config dict suitable for passing to checkpointer operations.
    """
    return {"configurable": {"thread_id": conversation_id}}


async def load_checkpoint(
    checkpointer: AsyncPostgresSaver,
    conversation_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any | None:
    """Load the latest checkpoint for a conversation with a 5-second timeout.

    On failure (timeout, DB error, connection error):
      1. Set the escalation flag on the conversation transactionally (Req 2.7)
      2. Only after escalation is persisted, return control so the caller
         can write the audit log (Req 2.7)
      3. If setting the escalation flag fails, raise EscalationSetError
         so the caller does NOT write audit and surfaces the raw error (Req 2.8)

    Args:
        checkpointer: The AsyncPostgresSaver instance.
        conversation_id: The conversation UUID string.
        session_factory: SQLAlchemy async session factory for escalation writes.

    Returns:
        The checkpoint tuple if found, None if no checkpoint exists.

    Raises:
        CheckpointLoadError: When the load operation fails AND the escalation
            flag was successfully set. The caller should write audit and stop.
        EscalationSetError: When setting the escalation flag fails after a
            load failure. The caller MUST NOT write audit and MUST surface
            the failure for webhook re-delivery.
    """
    config = make_thread_config(conversation_id)

    try:
        result = await asyncio.wait_for(
            checkpointer.aget_tuple(config),
            timeout=CHECKPOINT_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError as exc:
        reason = "Checkpoint load timed out (exceeded 5s)"
        logger.error(
            "checkpoint_load_timeout",
            conversation_id=conversation_id,
        )
        await _handle_load_failure(
            conversation_id=conversation_id,
            reason=reason,
            session_factory=session_factory,
            original_error=exc,
        )
        # _handle_load_failure always raises
        raise  # pragma: no cover — unreachable, satisfies type checker
    except Exception as exc:
        reason = f"Checkpoint load error: {type(exc).__name__}: {exc}"
        logger.error(
            "checkpoint_load_error",
            conversation_id=conversation_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _handle_load_failure(
            conversation_id=conversation_id,
            reason=reason,
            session_factory=session_factory,
            original_error=exc,
        )
        # _handle_load_failure always raises
        raise  # pragma: no cover — unreachable, satisfies type checker


async def commit_checkpoint(
    checkpointer: AsyncPostgresSaver,
    conversation_id: str,
    config: dict[str, Any],
    checkpoint: dict[str, Any],
    metadata: dict[str, Any],
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any:
    """Commit a checkpoint with a 5-second timeout.

    On failure (timeout, DB error, connection error):
      - Set the escalation flag on the conversation (Req 2.9)
      - Raise CheckpointCommitError so the caller does NOT send the reply

    Args:
        checkpointer: The AsyncPostgresSaver instance.
        conversation_id: The conversation UUID string.
        config: The LangGraph config dict (with thread_id).
        checkpoint: The checkpoint data to persist.
        metadata: Metadata to store with the checkpoint.
        session_factory: SQLAlchemy async session factory for escalation writes.

    Returns:
        The result from the checkpointer's aput operation.

    Raises:
        CheckpointCommitError: When the commit fails. The caller MUST NOT
            send the outbound reply and MUST surface a non-success response.
    """
    try:
        result = await asyncio.wait_for(
            checkpointer.aput(config, checkpoint, metadata),
            timeout=CHECKPOINT_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        reason = "Checkpoint commit timed out (exceeded 5s)"
        logger.error(
            "checkpoint_commit_timeout",
            conversation_id=conversation_id,
        )
        await _handle_commit_failure(
            conversation_id=conversation_id,
            reason=reason,
            session_factory=session_factory,
        )
        # _handle_commit_failure always raises
        raise  # pragma: no cover
    except Exception as exc:
        reason = f"Checkpoint commit error: {type(exc).__name__}: {exc}"
        logger.error(
            "checkpoint_commit_error",
            conversation_id=conversation_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _handle_commit_failure(
            conversation_id=conversation_id,
            reason=reason,
            session_factory=session_factory,
        )
        # _handle_commit_failure always raises
        raise  # pragma: no cover


async def _handle_load_failure(
    *,
    conversation_id: str,
    reason: str,
    session_factory: async_sessionmaker[AsyncSession],
    original_error: Exception,
) -> None:
    """Handle a checkpoint load failure per Req 2.7 and 2.8.

    Sets the escalation flag transactionally. If that succeeds, raises
    CheckpointLoadError so the caller can write audit and stop processing.
    If setting the escalation flag fails, raises EscalationSetError so the
    caller does NOT write audit and surfaces the raw failure.
    """
    try:
        async with session_factory() as session:
            await set_escalation(
                session,
                uuid.UUID(conversation_id),
                reason=f"Checkpoint load failure: {reason}",
            )
            await session.commit()
    except Exception as esc_exc:
        logger.error(
            "escalation_set_failed_after_load_failure",
            conversation_id=conversation_id,
            escalation_error=str(esc_exc),
            original_error=str(original_error),
        )
        raise EscalationSetError(
            conversation_id=conversation_id,
            reason=f"Could not set escalation flag: {esc_exc}",
        ) from esc_exc

    # Escalation flag set successfully — raise CheckpointLoadError
    # so the caller knows to write audit and stop processing
    raise CheckpointLoadError(
        conversation_id=conversation_id,
        reason=reason,
    )


async def _handle_commit_failure(
    *,
    conversation_id: str,
    reason: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Handle a checkpoint commit failure per Req 2.9.

    Sets the escalation flag and raises CheckpointCommitError so the caller
    does NOT send the outbound reply and surfaces a non-success response.
    """
    try:
        async with session_factory() as session:
            await set_escalation(
                session,
                uuid.UUID(conversation_id),
                reason=f"Checkpoint commit failure: {reason}",
            )
            await session.commit()
    except Exception as esc_exc:
        logger.error(
            "escalation_set_failed_after_commit_failure",
            conversation_id=conversation_id,
            escalation_error=str(esc_exc),
        )
        # Even if escalation fails, we still raise CheckpointCommitError
        # to prevent the reply from being sent (Req 2.9)
        raise CheckpointCommitError(
            conversation_id=conversation_id,
            reason=f"{reason} (escalation also failed: {esc_exc})",
        ) from esc_exc

    raise CheckpointCommitError(
        conversation_id=conversation_id,
        reason=reason,
    )

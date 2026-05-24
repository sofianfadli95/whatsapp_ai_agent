"""Audit logger for emitting structured audit records.

Provides an AuditLogger class that writes audit records to the database
via the repository layer. Designed to be non-blocking for the caller
by accepting a session factory and writing within its own session scope.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.audit import insert_audit_log

logger = structlog.stdlib.get_logger(__name__)


class AuditLogger:
    """Emits structured audit records to the audit_logs table.

    Uses its own session scope so callers don't need to manage
    the audit transaction alongside their business transaction.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def emit(
        self,
        *,
        actor: str,
        event_type: str,
        tool_name: str | None = None,
        conversation_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        request_id: str | None = None,
        dedupe_key: str | None = None,
        input_redacted: dict[str, Any] | None = None,
        output_redacted: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Persist an audit log record.

        On failure, logs the error but does not raise — audit writes
        should not break the caller's flow.
        """
        try:
            async with self._session_factory() as session:
                await insert_audit_log(
                    session,
                    actor=actor,
                    event_type=event_type,
                    tool_name=tool_name,
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                    order_id=order_id,
                    request_id=request_id,
                    dedupe_key=dedupe_key,
                    input_redacted=input_redacted,
                    output_redacted=output_redacted,
                    error_code=error_code,
                )
                await session.commit()
        except Exception as exc:
            logger.error(
                "audit_logger_emit_failed",
                event_type=event_type,
                error=str(exc),
            )

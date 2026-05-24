"""Repository for AuditLog and AuditLogFailure aggregates.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, AuditLogFailure


async def insert_audit_log(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    tool_name: str | None = None,
    conversation_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    request_id: str | None = None,
    dedupe_key: str | None = None,
    input_redacted: dict | None = None,
    output_redacted: dict | None = None,
    error_code: str | None = None,
) -> AuditLog:
    """Insert an audit log record."""
    log = AuditLog(
        id=uuid.uuid4(),
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
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(log)
    await session.flush()
    return log


async def insert_audit_log_failure(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    tool_name: str | None = None,
    conversation_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    request_id: str | None = None,
    dedupe_key: str | None = None,
    input_redacted: dict | None = None,
    output_redacted: dict | None = None,
    error_code: str | None = None,
    attempt_count: int = 0,
    last_error: str | None = None,
) -> AuditLogFailure:
    """Insert an audit log failure record (for the durable failure queue)."""
    failure = AuditLogFailure(
        id=uuid.uuid4(),
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
        occurred_at=datetime.now(timezone.utc),
        attempt_count=attempt_count,
        last_error=last_error,
    )
    session.add(failure)
    await session.flush()
    return failure


async def get_pending_failures(
    session: AsyncSession, *, limit: int = 50
) -> list[AuditLogFailure]:
    """Get pending audit log failures for retry."""
    stmt = (
        select(AuditLogFailure)
        .order_by(AuditLogFailure.occurred_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

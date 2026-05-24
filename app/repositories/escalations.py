"""Repository for Escalation aggregate.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Escalation


async def create_escalation(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    reason: str,
    actor: str,
    context_snapshot: dict | None = None,
) -> Escalation:
    """Create a new escalation record."""
    escalation = Escalation(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        reason=reason,
        actor=actor,
        context_snapshot=context_snapshot,
        is_active=True,
        set_at=datetime.now(timezone.utc),
    )
    session.add(escalation)
    await session.flush()
    return escalation


async def get_active_escalation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> Escalation | None:
    """Get the active escalation for a conversation."""
    stmt = select(Escalation).where(
        Escalation.conversation_id == conversation_id,
        Escalation.is_active.is_(True),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def clear_escalation(
    session: AsyncSession,
    escalation_id: uuid.UUID,
) -> bool:
    """Clear (deactivate) an escalation. Returns True if a row was updated."""
    stmt = (
        update(Escalation)
        .where(Escalation.id == escalation_id)
        .values(is_active=False, cleared_at=datetime.now(timezone.utc))
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0  # type: ignore[union-attr]


async def clear_all_for_conversation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> int:
    """Clear all active escalations for a conversation. Returns count of cleared."""
    stmt = (
        update(Escalation)
        .where(
            Escalation.conversation_id == conversation_id,
            Escalation.is_active.is_(True),
        )
        .values(is_active=False, cleared_at=datetime.now(timezone.utc))
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount  # type: ignore[union-attr]

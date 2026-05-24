"""Repository for Conversation aggregate.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation


async def get_by_customer(
    session: AsyncSession, customer_id: uuid.UUID
) -> Conversation | None:
    """Get the conversation for a customer (one conversation per customer)."""
    stmt = select(Conversation).where(Conversation.customer_id == customer_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_by_customer(
    session: AsyncSession, customer_id: uuid.UUID
) -> Conversation:
    """Get or create a conversation for a customer."""
    existing = await get_by_customer(session, customer_id)
    if existing is not None:
        return existing

    conversation = Conversation(
        id=uuid.uuid4(),
        customer_id=customer_id,
        escalation_flag=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def set_escalation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reason: str,
) -> None:
    """Set the escalation flag on a conversation."""
    stmt = (
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            escalation_flag=True,
            escalation_reason=reason,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def clear_escalation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> None:
    """Clear the escalation flag on a conversation."""
    stmt = (
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            escalation_flag=False,
            escalation_reason=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def update_last_message_at(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    timestamp: datetime | None = None,
) -> None:
    """Update the last_message_at timestamp."""
    ts = timestamp or datetime.now(timezone.utc)
    stmt = (
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(last_message_at=ts, updated_at=datetime.now(timezone.utc))
    )
    await session.execute(stmt)
    await session.flush()

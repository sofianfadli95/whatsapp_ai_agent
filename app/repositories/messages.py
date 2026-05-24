"""Repository for Messages (inbound and outbound).

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MessageInbound, MessageOutbound


async def insert_inbound_idempotent(
    session: AsyncSession,
    *,
    baileys_message_id: str,
    conversation_id: uuid.UUID | None,
    from_phone_raw: str,
    from_phone_e164: str | None,
    message_type: str,
    text_body: str | None,
    event_timestamp: datetime,
    raw_payload: dict,
) -> MessageInbound | None:
    """Insert an inbound message idempotently by baileys_message_id.

    Returns the MessageInbound if inserted, or None if it already existed
    (ON CONFLICT DO NOTHING semantics).
    """
    # Check if already exists
    stmt = select(MessageInbound).where(
        MessageInbound.baileys_message_id == baileys_message_id
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing is not None:
        return None  # Already exists — idempotent skip

    msg = MessageInbound(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        baileys_message_id=baileys_message_id,
        from_phone_raw=from_phone_raw,
        from_phone_e164=from_phone_e164,
        message_type=message_type,
        text_body=text_body,
        event_timestamp=event_timestamp,
        received_at=datetime.now(timezone.utc),
        raw_payload=raw_payload,
    )
    session.add(msg)
    await session.flush()
    return msg


async def insert_outbound(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    to_phone_e164: str,
    text_body: str,
    status: str = "queued",
) -> MessageOutbound:
    """Insert a new outbound message."""
    msg = MessageOutbound(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        to_phone_e164=to_phone_e164,
        text_body=text_body,
        status=status,
        attempts=0,
        created_at=datetime.now(timezone.utc),
    )
    session.add(msg)
    await session.flush()
    return msg


async def update_outbound_status(
    session: AsyncSession,
    *,
    whatsapp_message_id: str,
    status: str,
    sent_at: datetime | None = None,
) -> bool:
    """Update outbound message status by whatsapp_message_id.

    Returns True if a row was updated, False otherwise.
    """
    values: dict = {
        "status": status,
        "last_status_at": datetime.now(timezone.utc),
    }
    if sent_at is not None:
        values["sent_at"] = sent_at

    stmt = (
        update(MessageOutbound)
        .where(MessageOutbound.whatsapp_message_id == whatsapp_message_id)
        .values(**values)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0  # type: ignore[union-attr]

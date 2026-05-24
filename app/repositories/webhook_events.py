"""Repository for PaymentWebhookEvent aggregate.

All functions accept an AsyncSession and never call commit().
Uses ON CONFLICT DO NOTHING semantics for idempotent webhook processing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentWebhookEvent


async def try_insert(
    session: AsyncSession,
    *,
    webhook_event_id: str,
    payment_id: uuid.UUID | None,
    event_kind: str,
    raw_payload: dict,
    signature_header: str | None = None,
) -> PaymentWebhookEvent | None:
    """Try to insert a webhook event idempotently by webhook_event_id.

    Returns the PaymentWebhookEvent if inserted, or None if it already existed
    (ON CONFLICT DO NOTHING semantics).
    """
    # Check if already exists
    stmt = select(PaymentWebhookEvent).where(
        PaymentWebhookEvent.webhook_event_id == webhook_event_id
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing is not None:
        return None  # Already processed — idempotent skip

    now = datetime.now(timezone.utc)
    event = PaymentWebhookEvent(
        id=uuid.uuid4(),
        webhook_event_id=webhook_event_id,
        payment_id=payment_id,
        event_kind=event_kind,
        signature_header=signature_header,
        raw_payload=raw_payload,
        received_at=now,
        processed_at=now,
    )
    session.add(event)
    await session.flush()
    return event


async def get_by_webhook_event_id(
    session: AsyncSession, webhook_event_id: str
) -> PaymentWebhookEvent | None:
    """Get a webhook event by its external webhook_event_id."""
    stmt = select(PaymentWebhookEvent).where(
        PaymentWebhookEvent.webhook_event_id == webhook_event_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

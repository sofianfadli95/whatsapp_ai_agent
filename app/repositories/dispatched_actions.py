"""Repository for DispatchedAction aggregate.

All functions accept an AsyncSession and never call commit().
Uses ON CONFLICT DO NOTHING semantics for exactly-once dispatch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DispatchedAction


async def try_dispatch(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    action_type: str,
) -> DispatchedAction | None:
    """Try to dispatch an action idempotently by (order_id, action_type).

    Returns the DispatchedAction if inserted, or None if it already existed
    (ON CONFLICT DO NOTHING semantics — ensures exactly-once dispatch).
    """
    # Check if already dispatched
    stmt = select(DispatchedAction).where(
        DispatchedAction.order_id == order_id,
        DispatchedAction.action_type == action_type,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing is not None:
        return None  # Already dispatched — idempotent skip

    action = DispatchedAction(
        id=uuid.uuid4(),
        order_id=order_id,
        action_type=action_type,
        dispatched_at=datetime.now(timezone.utc),
    )
    session.add(action)
    await session.flush()
    return action


async def get_dispatched_actions_for_order(
    session: AsyncSession, order_id: uuid.UUID
) -> list[DispatchedAction]:
    """Get all dispatched actions for an order."""
    stmt = select(DispatchedAction).where(DispatchedAction.order_id == order_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())

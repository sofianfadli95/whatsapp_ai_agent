"""Repository for Shipment aggregate.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Shipment


async def create_shipment(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    tracking_number: str,
    status: str = "prepared",
) -> Shipment:
    """Create a new shipment record."""
    shipment = Shipment(
        id=uuid.uuid4(),
        order_id=order_id,
        tracking_number=tracking_number,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(shipment)
    await session.flush()
    return shipment


async def get_shipment_by_order(
    session: AsyncSession, order_id: uuid.UUID
) -> Shipment | None:
    """Get the shipment for an order."""
    stmt = select(Shipment).where(Shipment.order_id == order_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_shipment_by_tracking(
    session: AsyncSession, tracking_number: str
) -> Shipment | None:
    """Get a shipment by tracking number."""
    stmt = select(Shipment).where(Shipment.tracking_number == tracking_number)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_shipment_status(
    session: AsyncSession,
    shipment_id: uuid.UUID,
    status: str,
) -> bool:
    """Update shipment status. Returns True if a row was updated."""
    stmt = (
        update(Shipment)
        .where(Shipment.id == shipment_id)
        .values(status=status, updated_at=datetime.now(timezone.utc))
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0  # type: ignore[union-attr]

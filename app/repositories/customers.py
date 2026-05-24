"""Repository for Customer aggregate.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer


async def upsert(session: AsyncSession, phone_e164: str) -> Customer:
    """Upsert a customer by phone_e164. Returns existing or newly created."""
    stmt = select(Customer).where(Customer.phone_e164 == phone_e164)
    result = await session.execute(stmt)
    customer = result.scalar_one_or_none()
    if customer is not None:
        return customer

    customer = Customer(
        id=uuid.uuid4(),
        phone_e164=phone_e164,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(customer)
    await session.flush()
    return customer


async def get_by_id(session: AsyncSession, customer_id: uuid.UUID) -> Customer | None:
    """Get a customer by ID."""
    stmt = select(Customer).where(Customer.id == customer_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_phone(session: AsyncSession, phone_e164: str) -> Customer | None:
    """Get a customer by phone number."""
    stmt = select(Customer).where(Customer.phone_e164 == phone_e164)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

"""Repository for Payment aggregate.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment


async def create_payment(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    provider_ref: str,
    link_url: str,
    status: str = "created",
    amount: Decimal,
    currency: str,
    expires_at: datetime,
) -> Payment:
    """Create a new payment record."""
    payment = Payment(
        id=uuid.uuid4(),
        order_id=order_id,
        provider=provider,
        provider_ref=provider_ref,
        link_url=link_url,
        status=status,
        amount=amount,
        currency=currency,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(payment)
    await session.flush()
    return payment


async def get_payment_by_id(
    session: AsyncSession, payment_id: uuid.UUID, *, for_update: bool = False
) -> Payment | None:
    """Get a payment by ID, optionally with FOR UPDATE lock."""
    stmt = select(Payment).where(Payment.id == payment_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_payments_by_order(
    session: AsyncSession, order_id: uuid.UUID
) -> list[Payment]:
    """Get all payments for an order."""
    stmt = select(Payment).where(Payment.order_id == order_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_payment_status(
    session: AsyncSession,
    payment_id: uuid.UUID,
    status: str,
    *,
    paid_at: datetime | None = None,
    verification_metadata: dict | None = None,
) -> bool:
    """Update payment status. Returns True if a row was updated."""
    values: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if paid_at is not None:
        values["paid_at"] = paid_at
    if verification_metadata is not None:
        values["verification_metadata"] = verification_metadata
    stmt = update(Payment).where(Payment.id == payment_id).values(**values)
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0  # type: ignore[union-attr]

"""Repository for Order and OrderItem aggregates.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem


async def create_order(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    cart_id: uuid.UUID,
    status: str = "pending_payment",
    total: Decimal,
    currency: str,
    backend_fees: Decimal = Decimal("0"),
) -> Order:
    """Create a new order."""
    order = Order(
        id=uuid.uuid4(),
        customer_id=customer_id,
        cart_id=cart_id,
        status=status,
        total=total,
        currency=currency,
        backend_fees=backend_fees,
        created_at=datetime.now(timezone.utc),
    )
    session.add(order)
    await session.flush()
    return order


async def get_order_by_id(
    session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False
) -> Order | None:
    """Get an order by ID, optionally with FOR UPDATE lock."""
    stmt = select(Order).where(Order.id == order_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_order_status(
    session: AsyncSession,
    order_id: uuid.UUID,
    status: str,
    *,
    paid_at: datetime | None = None,
    shipment_prepared_at: datetime | None = None,
) -> bool:
    """Update order status. Returns True if a row was updated."""
    values: dict = {"status": status}
    if paid_at is not None:
        values["paid_at"] = paid_at
    if shipment_prepared_at is not None:
        values["shipment_prepared_at"] = shipment_prepared_at
    stmt = update(Order).where(Order.id == order_id).values(**values)
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0  # type: ignore[union-attr]


async def create_order_item(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    quantity: int,
    unit_price_snapshot: Decimal,
    currency: str,
    line_total: Decimal,
) -> OrderItem:
    """Create an order item."""
    item = OrderItem(
        id=uuid.uuid4(),
        order_id=order_id,
        product_id=product_id,
        variant_id=variant_id,
        quantity=quantity,
        unit_price_snapshot=unit_price_snapshot,
        currency=currency,
        line_total=line_total,
    )
    session.add(item)
    await session.flush()
    return item


async def get_order_items(
    session: AsyncSession, order_id: uuid.UUID
) -> list[OrderItem]:
    """Get all items for an order."""
    stmt = select(OrderItem).where(OrderItem.order_id == order_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())

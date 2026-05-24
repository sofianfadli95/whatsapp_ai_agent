"""Repository for Cart and CartItem aggregates.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cart, CartItem


async def get_active_cart(
    session: AsyncSession, customer_id: uuid.UUID
) -> Cart | None:
    """Get the active (OPEN) cart for a customer. Uses SELECT FOR UPDATE semantics."""
    stmt = (
        select(Cart)
        .where(Cart.customer_id == customer_id, Cart.status == "OPEN")
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_cart_by_id(
    session: AsyncSession, cart_id: uuid.UUID, *, for_update: bool = False
) -> Cart | None:
    """Get a cart by ID, optionally with FOR UPDATE lock."""
    stmt = select(Cart).where(Cart.id == cart_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_cart(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    currency: str = "IDR",
) -> Cart:
    """Create a new OPEN cart for a customer."""
    cart = Cart(
        id=uuid.uuid4(),
        customer_id=customer_id,
        status="OPEN",
        subtotal=Decimal("0"),
        currency=currency,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(cart)
    await session.flush()
    return cart


async def update_cart_subtotal(
    session: AsyncSession,
    cart_id: uuid.UUID,
    subtotal: Decimal,
) -> None:
    """Update the subtotal of a cart."""
    stmt = (
        update(Cart)
        .where(Cart.id == cart_id)
        .values(subtotal=subtotal, updated_at=datetime.now(timezone.utc))
    )
    await session.execute(stmt)
    await session.flush()


async def update_cart_status(
    session: AsyncSession,
    cart_id: uuid.UUID,
    status: str,
    *,
    order_id: uuid.UUID | None = None,
) -> None:
    """Update the status of a cart (e.g., OPEN -> CONVERTED)."""
    values: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if order_id is not None:
        values["order_id"] = order_id
    stmt = update(Cart).where(Cart.id == cart_id).values(**values)
    await session.execute(stmt)
    await session.flush()


async def add_cart_item(
    session: AsyncSession,
    *,
    cart_id: uuid.UUID,
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    quantity: int,
    unit_price_snapshot: Decimal,
    currency: str,
) -> CartItem:
    """Add an item to a cart."""
    item = CartItem(
        id=uuid.uuid4(),
        cart_id=cart_id,
        product_id=product_id,
        variant_id=variant_id,
        quantity=quantity,
        unit_price_snapshot=unit_price_snapshot,
        currency=currency,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.flush()
    return item


async def get_cart_items(
    session: AsyncSession, cart_id: uuid.UUID
) -> list[CartItem]:
    """Get all items in a cart."""
    stmt = select(CartItem).where(CartItem.cart_id == cart_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_cart_item_by_variant(
    session: AsyncSession, cart_id: uuid.UUID, variant_id: uuid.UUID
) -> CartItem | None:
    """Get a specific cart item by cart and variant."""
    stmt = select(CartItem).where(
        CartItem.cart_id == cart_id, CartItem.variant_id == variant_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_cart_item_quantity(
    session: AsyncSession,
    item_id: uuid.UUID,
    quantity: int,
) -> None:
    """Update the quantity of a cart item."""
    stmt = (
        update(CartItem)
        .where(CartItem.id == item_id)
        .values(quantity=quantity, updated_at=datetime.now(timezone.utc))
    )
    await session.execute(stmt)
    await session.flush()


async def remove_cart_item(
    session: AsyncSession, item_id: uuid.UUID
) -> None:
    """Remove a cart item."""
    stmt = delete(CartItem).where(CartItem.id == item_id)
    await session.execute(stmt)
    await session.flush()

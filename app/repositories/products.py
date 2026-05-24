"""Repository for Product and ProductVariant aggregates.

All functions accept an AsyncSession and never call commit().
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product, ProductVariant


async def get_product_by_id(
    session: AsyncSession, product_id: uuid.UUID
) -> Product | None:
    """Get a product by ID."""
    stmt = select(Product).where(Product.id == product_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_by_sku(session: AsyncSession, sku: str) -> Product | None:
    """Get a product by SKU."""
    stmt = select(Product).where(Product.sku == sku)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_variant_by_id(
    session: AsyncSession, variant_id: uuid.UUID
) -> ProductVariant | None:
    """Get a product variant by ID."""
    stmt = select(ProductVariant).where(ProductVariant.id == variant_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_variant_by_sku(
    session: AsyncSession, variant_sku: str
) -> ProductVariant | None:
    """Get a product variant by variant SKU."""
    stmt = select(ProductVariant).where(ProductVariant.variant_sku == variant_sku)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_variants_by_product(
    session: AsyncSession, product_id: uuid.UUID
) -> list[ProductVariant]:
    """List all variants for a product."""
    stmt = select(ProductVariant).where(ProductVariant.product_id == product_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_active_products(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> list[Product]:
    """List active products with pagination."""
    stmt = (
        select(Product)
        .where(Product.is_active.is_(True))
        .order_by(Product.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

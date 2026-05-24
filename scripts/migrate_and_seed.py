#!/usr/bin/env python3
"""Migrate database and seed initial data for local development.

Steps:
  (a) Run `alembic upgrade head` to apply all migrations.
  (b) Ensure pgvector extension is created.
  (c) Seed ≥5 products with ≥1 variant each (idempotent via ON CONFLICT DO NOTHING).
  (d) Seed ≥5 FAQ documents (idempotent).
  (e) Placeholder for embedding computation (Phase 8 wires the actual generator).

Idempotent: a second run produces no new rows.
Exit non-zero with descriptive log on failure.

Usage:
    uv run python scripts/migrate_and_seed.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid5

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure project root is on sys.path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if DATABASE_URL not already set
if not os.environ.get("DATABASE_URL"):
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate_and_seed")


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_PRODUCTS: list[dict[str, Any]] = [
    {
        "sku": "PROD-MOISTURIZER-001",
        "name": "Hydra Glow Moisturizer",
        "description": "A lightweight daily moisturizer with hyaluronic acid and vitamin E. Suitable for all skin types. Absorbs quickly without leaving a greasy residue.",
        "category": "skincare",
        "variants": [
            {
                "variant_sku": "MOIST-50ML-REG",
                "attributes": {"size": "50ml", "type": "regular"},
                "current_price": 89000,
                "currency": "IDR",
                "available_stock": 150,
            },
            {
                "variant_sku": "MOIST-100ML-REG",
                "attributes": {"size": "100ml", "type": "regular"},
                "current_price": 149000,
                "currency": "IDR",
                "available_stock": 80,
            },
        ],
    },
    {
        "sku": "PROD-SERUM-001",
        "name": "Vitamin C Brightening Serum",
        "description": "A potent 15% vitamin C serum that brightens skin tone, reduces dark spots, and provides antioxidant protection. Best used in the morning routine.",
        "category": "skincare",
        "variants": [
            {
                "variant_sku": "SERUM-VC-30ML",
                "attributes": {"size": "30ml", "concentration": "15%"},
                "current_price": 175000,
                "currency": "IDR",
                "available_stock": 200,
            },
        ],
    },
    {
        "sku": "PROD-SUNSCREEN-001",
        "name": "UV Shield SPF50+ Sunscreen",
        "description": "Broad-spectrum SPF50+ PA++++ sunscreen with a lightweight, non-greasy formula. Water-resistant for up to 80 minutes. No white cast.",
        "category": "skincare",
        "variants": [
            {
                "variant_sku": "SUN-50ML-SPF50",
                "attributes": {"size": "50ml", "spf": "50+"},
                "current_price": 125000,
                "currency": "IDR",
                "available_stock": 300,
            },
            {
                "variant_sku": "SUN-30ML-SPF50",
                "attributes": {"size": "30ml", "spf": "50+"},
                "current_price": 79000,
                "currency": "IDR",
                "available_stock": 500,
            },
        ],
    },
    {
        "sku": "PROD-CLEANSER-001",
        "name": "Gentle Foam Cleanser",
        "description": "A pH-balanced gentle foam cleanser that removes dirt and makeup without stripping natural oils. Contains ceramides and green tea extract.",
        "category": "skincare",
        "variants": [
            {
                "variant_sku": "CLEAN-150ML-FOAM",
                "attributes": {"size": "150ml", "type": "foam"},
                "current_price": 65000,
                "currency": "IDR",
                "available_stock": 250,
            },
        ],
    },
    {
        "sku": "PROD-TONER-001",
        "name": "Rose Water Hydrating Toner",
        "description": "An alcohol-free hydrating toner with rose water and niacinamide. Helps minimize pores and balance skin pH after cleansing.",
        "category": "skincare",
        "variants": [
            {
                "variant_sku": "TONER-200ML-ROSE",
                "attributes": {"size": "200ml", "type": "hydrating"},
                "current_price": 95000,
                "currency": "IDR",
                "available_stock": 180,
            },
        ],
    },
    {
        "sku": "PROD-MASK-001",
        "name": "Clay Detox Face Mask",
        "description": "A deep-cleansing kaolin clay mask that draws out impurities and excess oil. Use 2-3 times per week for best results. Suitable for oily and combination skin.",
        "category": "skincare",
        "variants": [
            {
                "variant_sku": "MASK-100G-CLAY",
                "attributes": {"size": "100g", "type": "clay"},
                "current_price": 110000,
                "currency": "IDR",
                "available_stock": 120,
            },
        ],
    },
]

SEED_FAQ_DOCUMENTS: list[dict[str, str]] = [
    {
        "title": "Shipping Policy",
        "body": (
            "We ship all orders within 1-2 business days after payment confirmation. "
            "Standard shipping takes 3-5 business days for Java and 5-7 business days "
            "for other islands. Free shipping is available for orders above IDR 200,000. "
            "Tracking numbers are provided via WhatsApp once the shipment is prepared."
        ),
    },
    {
        "title": "Return and Refund Policy",
        "body": (
            "We accept returns within 7 days of delivery for unopened products in original "
            "packaging. To initiate a return, please contact our customer service via WhatsApp. "
            "Refunds are processed within 5-7 business days after we receive the returned item. "
            "Opened or used products cannot be returned unless they are defective."
        ),
    },
    {
        "title": "Payment Methods",
        "body": (
            "We accept payments via bank transfer (BCA, Mandiri, BNI, BRI), e-wallets "
            "(GoPay, OVO, DANA, ShopeePay), and credit/debit cards (Visa, Mastercard). "
            "Payment links are valid for 24 hours. If your payment link expires, please "
            "request a new one through our WhatsApp chat."
        ),
    },
    {
        "title": "How to Use Our Products",
        "body": (
            "For best results, follow this skincare routine order: 1) Cleanser - wash face "
            "with gentle foam cleanser. 2) Toner - apply hydrating toner with cotton pad. "
            "3) Serum - apply vitamin C serum in the morning or treatment serum at night. "
            "4) Moisturizer - lock in hydration with moisturizer. 5) Sunscreen - always "
            "apply SPF50+ sunscreen as the last step in your morning routine."
        ),
    },
    {
        "title": "Product Ingredients and Allergens",
        "body": (
            "All our products are dermatologically tested, cruelty-free, and free from "
            "parabens, sulfates, and artificial fragrances. Full ingredient lists are "
            "available on each product page. If you have sensitive skin or known allergies, "
            "we recommend doing a patch test before full application. Contact us via "
            "WhatsApp for specific ingredient inquiries."
        ),
    },
    {
        "title": "Warranty and Product Authenticity",
        "body": (
            "All products sold through our store are 100% authentic and sourced directly "
            "from manufacturers. Each product comes with a batch number and expiry date. "
            "If you suspect you received a counterfeit product, please contact us immediately "
            "with photos and your order number for investigation."
        ),
    },
]


# Namespace UUID for deterministic seed IDs (prevents duplicates across runs)
SEED_NAMESPACE = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def deterministic_id(namespace_label: str, key: str) -> str:
    """Generate a deterministic UUID5 from a namespace label and key."""
    ns = uuid5(SEED_NAMESPACE, namespace_label)
    return str(uuid5(ns, key))


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------


def step_run_migrations() -> None:
    """Run alembic upgrade head."""
    logger.info("Step 1: Running alembic upgrade head...")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Alembic migration failed:\nstdout: %s\nstderr: %s", result.stdout, result.stderr)
        raise RuntimeError(f"alembic upgrade head failed with exit code {result.returncode}")
    logger.info("Migrations applied successfully.")


async def step_ensure_pgvector(session: AsyncSession) -> None:
    """Ensure pgvector extension exists."""
    logger.info("Step 2: Ensuring pgvector extension...")
    await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await session.commit()
    logger.info("pgvector extension ensured.")


async def step_seed_products(session: AsyncSession) -> int:
    """Seed products and variants. Returns number of new products inserted."""
    logger.info("Step 3: Seeding products and variants...")
    inserted_products = 0
    inserted_variants = 0

    for product_data in SEED_PRODUCTS:
        product_id = deterministic_id("product", product_data["sku"])

        # Insert product (idempotent on sku)
        result = await session.execute(
            text("""
                INSERT INTO products (id, sku, name, description, category)
                VALUES (:id, :sku, :name, :description, :category)
                ON CONFLICT (sku) DO NOTHING
                RETURNING id
            """),
            {
                "id": product_id,
                "sku": product_data["sku"],
                "name": product_data["name"],
                "description": product_data["description"],
                "category": product_data["category"],
            },
        )
        row = result.fetchone()
        if row:
            inserted_products += 1

        # Insert variants (idempotent on variant_sku)
        for variant_data in product_data["variants"]:
            variant_id = deterministic_id("variant", variant_data["variant_sku"])
            variant_result = await session.execute(
                text("""
                    INSERT INTO product_variants
                        (id, product_id, variant_sku, attributes, current_price, currency, available_stock)
                    VALUES (:id, :product_id, :variant_sku, CAST(:attributes AS jsonb), :current_price, :currency, :available_stock)
                    ON CONFLICT (variant_sku) DO NOTHING
                    RETURNING id
                """),
                {
                    "id": variant_id,
                    "product_id": product_id,
                    "variant_sku": variant_data["variant_sku"],
                    "attributes": json.dumps(variant_data["attributes"]),
                    "current_price": variant_data["current_price"],
                    "currency": variant_data["currency"],
                    "available_stock": variant_data["available_stock"],
                },
            )
            if variant_result.fetchone():
                inserted_variants += 1

    await session.commit()
    logger.info(
        "Products seeded: %d new products, %d new variants (total seed: %d products, %d variants).",
        inserted_products,
        inserted_variants,
        len(SEED_PRODUCTS),
        sum(len(p["variants"]) for p in SEED_PRODUCTS),
    )
    return inserted_products


async def step_seed_faq_documents(session: AsyncSession) -> int:
    """Seed FAQ documents. Returns number of new documents inserted."""
    logger.info("Step 4: Seeding FAQ documents...")
    inserted = 0

    for faq_data in SEED_FAQ_DOCUMENTS:
        faq_id = deterministic_id("faq", faq_data["title"])
        result = await session.execute(
            text("""
                INSERT INTO faq_documents (id, title, body)
                VALUES (:id, :title, :body)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
            """),
            {
                "id": faq_id,
                "title": faq_data["title"],
                "body": faq_data["body"],
            },
        )
        if result.fetchone():
            inserted += 1

    await session.commit()
    logger.info(
        "FAQ documents seeded: %d new documents (total seed: %d).",
        inserted,
        len(SEED_FAQ_DOCUMENTS),
    )
    return inserted


async def step_compute_embeddings(session: AsyncSession) -> None:
    """Placeholder for embedding computation.

    Phase 8 wires the actual embedding generator. For now, this step
    logs that embedding computation is deferred and does nothing.
    """
    logger.info(
        "Step 5: Embedding computation placeholder — "
        "actual embedding generation will be wired in Phase 8."
    )
    # TODO(Phase 8): Compute and upsert embeddings for any product/FAQ row
    # updated since last run. Use the configured EMBEDDING_MODEL to generate
    # vector embeddings and insert into product_embeddings / faq_embeddings.


async def run_seed() -> None:
    """Run all seed steps after migrations."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Set it to a valid PostgreSQL connection string (e.g., "
            "postgresql+asyncpg://postgres:postgres@localhost:5432/wa_sales_agent)."
        )

    engine = create_async_engine(database_url, pool_pre_ping=True, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_factory() as session:
            await step_ensure_pgvector(session)

        async with session_factory() as session:
            await step_seed_products(session)

        async with session_factory() as session:
            await step_seed_faq_documents(session)

        async with session_factory() as session:
            await step_compute_embeddings(session)

        # Report final counts for verification
        async with session_factory() as session:
            product_count = (await session.execute(text("SELECT count(*) FROM products"))).scalar()
            variant_count = (await session.execute(text("SELECT count(*) FROM product_variants"))).scalar()
            faq_count = (await session.execute(text("SELECT count(*) FROM faq_documents"))).scalar()
            logger.info(
                "Final counts — products: %d, variants: %d, faq_documents: %d",
                product_count,
                variant_count,
                faq_count,
            )
    finally:
        await engine.dispose()


def main() -> None:
    """Main entry point: run migrations then seed."""
    try:
        step_run_migrations()
        asyncio.run(run_seed())
        logger.info("migrate_and_seed completed successfully.")
    except Exception as exc:
        logger.error("migrate_and_seed FAILED: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

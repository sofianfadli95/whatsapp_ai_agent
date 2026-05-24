"""Unit tests for tool audit-exactly-once behavior.

Verification: each tool invocation emits exactly one audit record via
AuditLogger.emit, keyed by a tool-invocation id (Property 25).

Tests cover both success and failure paths for all tools.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.tools.catalog import build_catalog_tools
from app.tools.order import build_order_tools
from app.tools.payment import build_payment_tools
from app.tools.support import build_support_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_logger() -> AsyncMock:
    """Mock audit logger that tracks emit calls."""
    mock = AsyncMock()
    mock.emit = AsyncMock()
    return mock


@pytest.fixture
def catalog_service() -> AsyncMock:
    """Mock catalog service."""
    mock = AsyncMock()
    mock.search = AsyncMock(return_value={"products": []})
    mock.add_to_cart = AsyncMock(return_value={"cart_id": "cart-1", "items": []})
    mock.update_cart_item_quantity = AsyncMock(
        return_value={"cart_id": "cart-1", "items": []}
    )
    mock.remove_from_cart = AsyncMock(return_value={"cart_id": "cart-1", "items": []})
    mock.get_cart = AsyncMock(return_value={"cart_id": "cart-1", "items": []})
    return mock


@pytest.fixture
def order_service() -> AsyncMock:
    """Mock order service."""
    mock = AsyncMock()
    mock.create_order = AsyncMock(
        return_value={"order_id": "order-1", "total": "100.00", "currency": "IDR"}
    )
    return mock


@pytest.fixture
def payment_service() -> AsyncMock:
    """Mock payment service."""
    mock = AsyncMock()
    mock.create_payment_link = AsyncMock(
        return_value={"link_url": "https://pay.example.com/abc", "expires_at": "2025-01-01T00:00:00Z"}
    )
    return mock


@pytest.fixture
def human_support_service() -> AsyncMock:
    """Mock human support service."""
    mock = AsyncMock()
    mock.escalate = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# Catalog tools — audit exactly once
# ---------------------------------------------------------------------------


class TestSearchProductsAudit:
    """search_products emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_catalog_tools(catalog_service, audit_logger)
        search_tool = tools[0]  # search_products

        await search_tool.ainvoke({"query": "moisturizer"})

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "search_products"
        assert call_kwargs["event_type"] == "tool_invocation_success"
        assert call_kwargs["dedupe_key"] is not None

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        catalog_service.search = AsyncMock(side_effect=RuntimeError("DB down"))
        tools = build_catalog_tools(catalog_service, audit_logger)
        search_tool = tools[0]

        result = await search_tool.ainvoke({"query": "moisturizer"})

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "search_products"
        assert call_kwargs["event_type"] == "tool_invocation_error"
        assert call_kwargs["error_code"] == "internal_error"
        assert result["ok"] is False


class TestAddToCartAudit:
    """add_to_cart emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_catalog_tools(catalog_service, audit_logger)
        add_tool = tools[1]  # add_to_cart

        await add_tool.ainvoke(
            {
                "customer_id": "cust-1",
                "product_id": "prod-1",
                "variant_id": "var-1",
                "quantity": 2,
            }
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "add_to_cart"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        catalog_service.add_to_cart = AsyncMock(
            side_effect=RuntimeError("stock check failed")
        )
        tools = build_catalog_tools(catalog_service, audit_logger)
        add_tool = tools[1]

        result = await add_tool.ainvoke(
            {
                "customer_id": "cust-1",
                "product_id": "prod-1",
                "variant_id": "var-1",
                "quantity": 2,
            }
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["event_type"] == "tool_invocation_error"
        assert result["ok"] is False


class TestUpdateCartItemQuantityAudit:
    """update_cart_item_quantity emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_catalog_tools(catalog_service, audit_logger)
        update_tool = tools[2]  # update_cart_item_quantity

        await update_tool.ainvoke(
            {"customer_id": "cust-1", "cart_item_id": "item-1", "quantity": 5}
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "update_cart_item_quantity"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        catalog_service.update_cart_item_quantity = AsyncMock(
            side_effect=ValueError("invalid")
        )
        tools = build_catalog_tools(catalog_service, audit_logger)
        update_tool = tools[2]

        result = await update_tool.ainvoke(
            {"customer_id": "cust-1", "cart_item_id": "item-1", "quantity": 5}
        )

        assert audit_logger.emit.call_count == 1
        assert result["ok"] is False


class TestRemoveFromCartAudit:
    """remove_from_cart emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_catalog_tools(catalog_service, audit_logger)
        remove_tool = tools[3]  # remove_from_cart

        await remove_tool.ainvoke(
            {"customer_id": "cust-1", "cart_item_id": "item-1"}
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "remove_from_cart"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        catalog_service.remove_from_cart = AsyncMock(
            side_effect=RuntimeError("not found")
        )
        tools = build_catalog_tools(catalog_service, audit_logger)
        remove_tool = tools[3]

        result = await remove_tool.ainvoke(
            {"customer_id": "cust-1", "cart_item_id": "item-1"}
        )

        assert audit_logger.emit.call_count == 1
        assert result["ok"] is False


class TestGetCartAudit:
    """get_cart emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_catalog_tools(catalog_service, audit_logger)
        get_tool = tools[4]  # get_cart

        await get_tool.ainvoke({"customer_id": "cust-1"})

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "get_cart"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        catalog_service.get_cart = AsyncMock(side_effect=RuntimeError("DB error"))
        tools = build_catalog_tools(catalog_service, audit_logger)
        get_tool = tools[4]

        result = await get_tool.ainvoke({"customer_id": "cust-1"})

        assert audit_logger.emit.call_count == 1
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Order tools — audit exactly once
# ---------------------------------------------------------------------------


class TestCreateOrderAudit:
    """create_order emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, order_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_order_tools(order_service, audit_logger)
        create_tool = tools[0]

        await create_tool.ainvoke(
            {
                "customer_id": "cust-1",
                "cart_id": "cart-1",
                "customer_confirmation_token": "token-abc",
            }
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "create_order"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, order_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        order_service.create_order = AsyncMock(
            side_effect=RuntimeError("token mismatch")
        )
        tools = build_order_tools(order_service, audit_logger)
        create_tool = tools[0]

        result = await create_tool.ainvoke(
            {
                "customer_id": "cust-1",
                "cart_id": "cart-1",
                "customer_confirmation_token": "token-abc",
            }
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["event_type"] == "tool_invocation_error"
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Payment tools — audit exactly once
# ---------------------------------------------------------------------------


class TestCreatePaymentLinkAudit:
    """create_payment_link emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, payment_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_payment_tools(payment_service, audit_logger)
        pay_tool = tools[0]

        await pay_tool.ainvoke({"order_id": "order-1"})

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "create_payment_link"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, payment_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        payment_service.create_payment_link = AsyncMock(
            side_effect=RuntimeError("provider unavailable")
        )
        tools = build_payment_tools(payment_service, audit_logger)
        pay_tool = tools[0]

        result = await pay_tool.ainvoke({"order_id": "order-1"})

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["event_type"] == "tool_invocation_error"
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Support tools — audit exactly once
# ---------------------------------------------------------------------------


class TestEscalateToHumanAudit:
    """escalate_to_human emits exactly one audit record per invocation."""

    @pytest.mark.asyncio
    async def test_success_emits_one_audit_record(
        self, human_support_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_support_tools(human_support_service, audit_logger)
        escalate_tool = tools[0]

        await escalate_tool.ainvoke(
            {"conversation_id": "conv-1", "reason": "Customer wants refund"}
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["tool_name"] == "escalate_to_human"
        assert call_kwargs["event_type"] == "tool_invocation_success"

    @pytest.mark.asyncio
    async def test_failure_emits_one_audit_record(
        self, human_support_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        human_support_service.escalate = AsyncMock(
            side_effect=RuntimeError("DB connection lost")
        )
        tools = build_support_tools(human_support_service, audit_logger)
        escalate_tool = tools[0]

        result = await escalate_tool.ainvoke(
            {"conversation_id": "conv-1", "reason": "Customer wants refund"}
        )

        assert audit_logger.emit.call_count == 1
        call_kwargs = audit_logger.emit.call_args.kwargs
        assert call_kwargs["event_type"] == "tool_invocation_error"
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Dedupe key uniqueness
# ---------------------------------------------------------------------------


class TestDedupeKeyUniqueness:
    """Each invocation gets a unique dedupe_key (tool-invocation id)."""

    @pytest.mark.asyncio
    async def test_two_invocations_get_different_dedupe_keys(
        self, catalog_service: AsyncMock, audit_logger: AsyncMock
    ) -> None:
        tools = build_catalog_tools(catalog_service, audit_logger)
        search_tool = tools[0]

        await search_tool.ainvoke({"query": "first"})
        await search_tool.ainvoke({"query": "second"})

        assert audit_logger.emit.call_count == 2
        key1 = audit_logger.emit.call_args_list[0].kwargs["dedupe_key"]
        key2 = audit_logger.emit.call_args_list[1].kwargs["dedupe_key"]
        assert key1 != key2

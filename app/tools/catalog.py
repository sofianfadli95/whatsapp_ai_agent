"""Catalog tools — LangChain-compatible tools wrapping CatalogService.

Tools: search_products, add_to_cart, update_cart_item_quantity, remove_from_cart, get_cart.
Each tool validates input via Pydantic, enforces an asyncio timeout, emits exactly one
audit record (success or failure), and returns a uniform ToolResult envelope.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, field_validator

from app.tools import (
    AuditLoggerProtocol,
    CatalogServiceProtocol,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Pydantic Input Schemas
# ---------------------------------------------------------------------------


class SearchProductsInput(BaseModel):
    """Input schema for search_products tool."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Search query string (1-200 characters).",
    )
    price_min: float | None = Field(
        default=None,
        ge=0,
        description="Minimum price filter (non-negative).",
    )
    price_max: float | None = Field(
        default=None,
        ge=0,
        description="Maximum price filter (non-negative).",
    )
    category: str | None = Field(
        default=None,
        max_length=100,
        description="Category filter.",
    )

    @field_validator("price_max")
    @classmethod
    def price_max_gte_min(cls, v: float | None, info: Any) -> float | None:
        price_min = info.data.get("price_min")
        if v is not None and price_min is not None and v < price_min:
            raise ValueError("price_max must be >= price_min")
        return v


class AddToCartInput(BaseModel):
    """Input schema for add_to_cart tool."""

    customer_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Customer identifier.",
    )
    product_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Product identifier.",
    )
    variant_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Product variant identifier.",
    )
    quantity: int = Field(
        ...,
        ge=1,
        le=999,
        description="Quantity to add (1-999).",
    )


class UpdateCartItemQuantityInput(BaseModel):
    """Input schema for update_cart_item_quantity tool."""

    customer_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Customer identifier.",
    )
    cart_item_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Cart item identifier.",
    )
    quantity: int = Field(
        ...,
        ge=1,
        le=999,
        description="New quantity (1-999).",
    )


class RemoveFromCartInput(BaseModel):
    """Input schema for remove_from_cart tool."""

    customer_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Customer identifier.",
    )
    cart_item_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Cart item identifier.",
    )


class GetCartInput(BaseModel):
    """Input schema for get_cart tool."""

    customer_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Customer identifier.",
    )


# ---------------------------------------------------------------------------
# Timeout constants (seconds)
# ---------------------------------------------------------------------------

SEARCH_PRODUCTS_TIMEOUT = 5.0  # catalog search timeout
ADD_TO_CART_TIMEOUT = 5.0
UPDATE_CART_ITEM_QUANTITY_TIMEOUT = 5.0
REMOVE_FROM_CART_TIMEOUT = 5.0
GET_CART_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_catalog_tools(
    catalog_service: CatalogServiceProtocol,
    audit_logger: AuditLoggerProtocol,
) -> list[StructuredTool]:
    """Build all catalog LangChain tools with injected dependencies."""

    async def _search_products(
        query: str,
        price_min: float | None = None,
        price_max: float | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {
            "query": query,
            "price_min": price_min,
            "price_max": price_max,
            "category": category,
        }
        try:
            result = await asyncio.wait_for(
                catalog_service.search(
                    query,
                    price_min=price_min,
                    price_max=price_max,
                    category=category,
                ),
                timeout=SEARCH_PRODUCTS_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="search_products",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure("timeout", "search_products timed out")
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="search_products",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="timeout",
            )
            return tool_result.to_dict()
        except Exception as exc:
            tool_result = ToolResult.failure("internal_error", str(exc))
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="search_products",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    async def _add_to_cart(
        customer_id: str,
        product_id: str,
        variant_id: str,
        quantity: int,
    ) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {
            "customer_id": customer_id,
            "product_id": product_id,
            "variant_id": variant_id,
            "quantity": quantity,
        }
        try:
            result = await asyncio.wait_for(
                catalog_service.add_to_cart(
                    customer_id,
                    product_id,
                    variant_id,
                    quantity,
                ),
                timeout=ADD_TO_CART_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="add_to_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure("timeout", "add_to_cart timed out")
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="add_to_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="timeout",
            )
            return tool_result.to_dict()
        except Exception as exc:
            tool_result = ToolResult.failure("internal_error", str(exc))
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="add_to_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    async def _update_cart_item_quantity(
        customer_id: str,
        cart_item_id: str,
        quantity: int,
    ) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {
            "customer_id": customer_id,
            "cart_item_id": cart_item_id,
            "quantity": quantity,
        }
        try:
            result = await asyncio.wait_for(
                catalog_service.update_cart_item_quantity(
                    customer_id,
                    cart_item_id,
                    quantity,
                ),
                timeout=UPDATE_CART_ITEM_QUANTITY_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="update_cart_item_quantity",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure(
                "timeout", "update_cart_item_quantity timed out"
            )
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="update_cart_item_quantity",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="timeout",
            )
            return tool_result.to_dict()
        except Exception as exc:
            tool_result = ToolResult.failure("internal_error", str(exc))
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="update_cart_item_quantity",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    async def _remove_from_cart(
        customer_id: str,
        cart_item_id: str,
    ) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {
            "customer_id": customer_id,
            "cart_item_id": cart_item_id,
        }
        try:
            result = await asyncio.wait_for(
                catalog_service.remove_from_cart(customer_id, cart_item_id),
                timeout=REMOVE_FROM_CART_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="remove_from_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure("timeout", "remove_from_cart timed out")
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="remove_from_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="timeout",
            )
            return tool_result.to_dict()
        except Exception as exc:
            tool_result = ToolResult.failure("internal_error", str(exc))
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="remove_from_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    async def _get_cart(customer_id: str) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {"customer_id": customer_id}
        try:
            result = await asyncio.wait_for(
                catalog_service.get_cart(customer_id),
                timeout=GET_CART_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="get_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure("timeout", "get_cart timed out")
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="get_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="timeout",
            )
            return tool_result.to_dict()
        except Exception as exc:
            tool_result = ToolResult.failure("internal_error", str(exc))
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="get_cart",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    # Build StructuredTool instances
    search_products_tool = StructuredTool.from_function(
        coroutine=_search_products,
        name="search_products",
        description="Search the product catalog by query with optional price and category filters.",
        args_schema=SearchProductsInput,
    )

    add_to_cart_tool = StructuredTool.from_function(
        coroutine=_add_to_cart,
        name="add_to_cart",
        description="Add a product variant to the customer's cart.",
        args_schema=AddToCartInput,
    )

    update_cart_item_quantity_tool = StructuredTool.from_function(
        coroutine=_update_cart_item_quantity,
        name="update_cart_item_quantity",
        description="Update the quantity of an existing cart item.",
        args_schema=UpdateCartItemQuantityInput,
    )

    remove_from_cart_tool = StructuredTool.from_function(
        coroutine=_remove_from_cart,
        name="remove_from_cart",
        description="Remove an item from the customer's cart.",
        args_schema=RemoveFromCartInput,
    )

    get_cart_tool = StructuredTool.from_function(
        coroutine=_get_cart,
        name="get_cart",
        description="Retrieve the customer's current active cart.",
        args_schema=GetCartInput,
    )

    return [
        search_products_tool,
        add_to_cart_tool,
        update_cart_item_quantity_tool,
        remove_from_cart_tool,
        get_cart_tool,
    ]

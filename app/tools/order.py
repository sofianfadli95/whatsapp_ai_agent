"""Order tools — LangChain-compatible tool wrapping OrderService.

Tool: create_order.
Validates input via Pydantic, enforces asyncio timeout, emits exactly one
audit record (success or failure), and returns a uniform ToolResult envelope.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.tools import (
    AuditLoggerProtocol,
    OrderServiceProtocol,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Pydantic Input Schema
# ---------------------------------------------------------------------------


class CreateOrderInput(BaseModel):
    """Input schema for create_order tool."""

    customer_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Customer identifier.",
    )
    cart_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Cart identifier.",
    )
    customer_confirmation_token: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Deterministic confirmation token derived from the cart snapshot.",
    )


# ---------------------------------------------------------------------------
# Timeout constant (seconds)
# ---------------------------------------------------------------------------

CREATE_ORDER_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_order_tools(
    order_service: OrderServiceProtocol,
    audit_logger: AuditLoggerProtocol,
) -> list[StructuredTool]:
    """Build order LangChain tools with injected dependencies."""

    async def _create_order(
        customer_id: str,
        cart_id: str,
        customer_confirmation_token: str,
    ) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {
            "customer_id": customer_id,
            "cart_id": cart_id,
            "customer_confirmation_token": customer_confirmation_token,
        }
        try:
            result = await asyncio.wait_for(
                order_service.create_order(
                    customer_id,
                    cart_id,
                    customer_confirmation_token,
                ),
                timeout=CREATE_ORDER_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="create_order",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure("timeout", "create_order timed out")
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="create_order",
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
                tool_name="create_order",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    create_order_tool = StructuredTool.from_function(
        coroutine=_create_order,
        name="create_order",
        description="Create an order from a confirmed cart with a valid confirmation token.",
        args_schema=CreateOrderInput,
    )

    return [create_order_tool]

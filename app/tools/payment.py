"""Payment tools — LangChain-compatible tool wrapping PaymentService.

Tool: create_payment_link.
Validates input via Pydantic, enforces asyncio timeout (10s), emits exactly one
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
    PaymentServiceProtocol,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Pydantic Input Schema
# ---------------------------------------------------------------------------


class CreatePaymentLinkInput(BaseModel):
    """Input schema for create_payment_link tool."""

    order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Order identifier for which to generate a payment link.",
    )


# ---------------------------------------------------------------------------
# Timeout constant (seconds)
# ---------------------------------------------------------------------------

CREATE_PAYMENT_LINK_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_payment_tools(
    payment_service: PaymentServiceProtocol,
    audit_logger: AuditLoggerProtocol,
) -> list[StructuredTool]:
    """Build payment LangChain tools with injected dependencies."""

    async def _create_payment_link(order_id: str) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {"order_id": order_id}
        try:
            result = await asyncio.wait_for(
                payment_service.create_payment_link(order_id),
                timeout=CREATE_PAYMENT_LINK_TIMEOUT,
            )
            tool_result = ToolResult.success(result)
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="create_payment_link",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure(
                "timeout", "create_payment_link timed out"
            )
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="create_payment_link",
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
                tool_name="create_payment_link",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    create_payment_link_tool = StructuredTool.from_function(
        coroutine=_create_payment_link,
        name="create_payment_link",
        description="Generate a payment link for an order that is pending payment.",
        args_schema=CreatePaymentLinkInput,
    )

    return [create_payment_link_tool]

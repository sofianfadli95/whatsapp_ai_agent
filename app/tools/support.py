"""Support tools — LangChain-compatible tool wrapping HumanSupportService.

Tool: escalate_to_human.
Validates input via Pydantic, enforces asyncio timeout (1s), emits exactly one
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
    HumanSupportServiceProtocol,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Pydantic Input Schema
# ---------------------------------------------------------------------------


class EscalateToHumanInput(BaseModel):
    """Input schema for escalate_to_human tool."""

    conversation_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Conversation identifier to escalate.",
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Reason for escalation.",
    )


# ---------------------------------------------------------------------------
# Timeout constant (seconds)
# ---------------------------------------------------------------------------

ESCALATE_TO_HUMAN_TIMEOUT = 1.0


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_support_tools(
    human_support_service: HumanSupportServiceProtocol,
    audit_logger: AuditLoggerProtocol,
) -> list[StructuredTool]:
    """Build support LangChain tools with injected dependencies."""

    async def _escalate_to_human(
        conversation_id: str,
        reason: str,
    ) -> dict[str, Any]:
        invocation_id = str(uuid.uuid4())
        input_data = {
            "conversation_id": conversation_id,
            "reason": reason,
        }
        try:
            await asyncio.wait_for(
                human_support_service.escalate(
                    conversation_id,
                    reason=reason,
                    actor="agent",
                ),
                timeout=ESCALATE_TO_HUMAN_TIMEOUT,
            )
            tool_result = ToolResult.success(
                {"conversation_id": conversation_id, "escalated": True}
            )
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_success",
                tool_name="escalate_to_human",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                output_redacted={"ok": True, "escalated": True},
            )
            return tool_result.to_dict()
        except asyncio.TimeoutError:
            tool_result = ToolResult.failure(
                "timeout", "escalate_to_human timed out"
            )
            await audit_logger.emit(
                actor="agent",
                event_type="tool_invocation_error",
                tool_name="escalate_to_human",
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
                tool_name="escalate_to_human",
                dedupe_key=invocation_id,
                input_redacted=input_data,
                error_code="internal_error",
            )
            return tool_result.to_dict()

    escalate_to_human_tool = StructuredTool.from_function(
        coroutine=_escalate_to_human,
        name="escalate_to_human",
        description="Escalate the conversation to a human support agent.",
        args_schema=EscalateToHumanInput,
    )

    return [escalate_to_human_tool]

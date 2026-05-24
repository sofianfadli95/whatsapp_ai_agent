"""Escalate node — sets escalation flag and informs the customer.

Handles all escalation scenarios: low confidence, consecutive failures,
explicit support requests, complaints, refunds.

Requirements: Req 10
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class EscalationService(Protocol):
    """Protocol for the escalation dependency."""

    async def escalate(self, conversation_id: str, reason: str) -> dict: ...


def build_escalate_node(
    escalation_service: EscalationService | None = None,
) -> Any:
    """Build the escalate node function.

    Args:
        escalation_service: Optional escalation service for persisting escalation.

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def escalate(state: ConversationState) -> dict[str, Any]:
        """Set escalation flag and prepare customer notification."""
        conversation_id = state.get("conversation_id", "")
        reason = state.get("escalation_reason", "Customer requires human assistance")

        # Persist escalation if service is available
        if escalation_service is not None:
            try:
                await escalation_service.escalate(
                    conversation_id=conversation_id,
                    reason=reason,
                )
            except Exception:
                # Even if persistence fails, we still set the flag in state
                pass

        return {
            "escalation_flag": True,
            "escalation_reason": reason,
            "reply_text": (
                "Saya akan menghubungkan Anda dengan tim kami yang dapat membantu lebih lanjut. "
                "Mohon tunggu sebentar, ya."
            ),
        }

    return escalate

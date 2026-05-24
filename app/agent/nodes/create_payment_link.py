"""Create-payment-link node — generates a payment link for the order.

Calls the payment service to create a payment link. Routes to send_reply
on success, or escalate if the provider is repeatedly unavailable.

Requirements: Req 7
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class PaymentService(Protocol):
    """Protocol for the payment link creation dependency."""

    async def create_payment_link(self, order_id: str) -> dict: ...


def build_create_payment_link_node(
    payment_service: PaymentService,
) -> Any:
    """Build the create_payment_link node function.

    Args:
        payment_service: A payment service (real or fake for testing).

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def create_payment_link(state: ConversationState) -> dict[str, Any]:
        """Generate a payment link for the created order."""
        order_id = state.get("last_order_id", "")

        if not order_id:
            return {
                "escalation_flag": True,
                "escalation_reason": "No order_id available for payment link creation",
            }

        try:
            result = await payment_service.create_payment_link(order_id=order_id)
        except Exception:
            # Provider unavailable → escalate
            return {
                "escalation_flag": True,
                "escalation_reason": "Payment provider unavailable",
            }

        if result.get("ok") is False:
            error_code = result.get("error", {}).get("code", "")
            if error_code == "provider_unavailable":
                return {
                    "escalation_flag": True,
                    "escalation_reason": "Payment provider unavailable repeatedly",
                }
            # Other errors → inform customer
            return {
                "reply_text": (
                    "Maaf, terjadi kendala saat membuat link pembayaran. "
                    "Tim kami akan segera membantu."
                ),
                "escalation_flag": True,
                "escalation_reason": f"Payment link error: {error_code}",
            }

        # Success — payment link created
        link_url = result.get("link_url", "")
        return {
            "last_payment_link": link_url,
            "reply_text": (
                f"Pesanan Anda telah dibuat! Silakan lakukan pembayaran melalui link berikut:\n\n"
                f"{link_url}\n\n"
                f"Link ini berlaku selama 24 jam."
            ),
        }

    return create_payment_link


def create_payment_link_edge(state: ConversationState) -> str:
    """Determine the next node after payment link creation."""
    if state.get("escalation_flag"):
        return "escalate"
    return "send_reply"

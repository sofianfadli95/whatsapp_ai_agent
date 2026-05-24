"""Create-order node — creates an order after customer confirmation.

Validates the confirmation token and calls the order service.
Routes to create_payment_link on success, send_reply on validation errors,
or escalate on unrecoverable failures.

Requirements: Req 6
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class OrderService(Protocol):
    """Protocol for the order creation dependency."""

    async def create_order(
        self, customer_id: str, cart_id: str, customer_confirmation_token: str
    ) -> dict: ...


def build_create_order_node(
    order_service: OrderService,
) -> Any:
    """Build the create_order node function.

    Args:
        order_service: An order service (real or fake for testing).

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def create_order(state: ConversationState) -> dict[str, Any]:
        """Create an order using the confirmation token."""
        customer_id = state.get("customer_id", "")
        cart_id = state.get("active_cart_id", "")
        token = state.get("pending_confirmation_token", "")

        if not cart_id or not token:
            return {
                "reply_text": (
                    "Maaf, tidak ada keranjang aktif atau token konfirmasi. "
                    "Silakan mulai proses checkout dari awal."
                ),
            }

        try:
            result = await order_service.create_order(
                customer_id=customer_id,
                cart_id=cart_id,
                customer_confirmation_token=token,
            )
        except Exception:
            # Unrecoverable error → escalate
            return {
                "escalation_flag": True,
                "escalation_reason": "Order creation failed with unrecoverable error",
            }

        # Check for structured errors from the service
        if result.get("ok") is False:
            error_code = result.get("error", {}).get("code", "")

            if error_code in ("token_mismatch", "price_changed", "insufficient_stock"):
                # Recoverable errors → inform customer
                error_messages = {
                    "token_mismatch": (
                        "Keranjang Anda telah berubah sejak konfirmasi terakhir. "
                        "Silakan review keranjang dan konfirmasi ulang."
                    ),
                    "price_changed": (
                        "Harga beberapa produk telah berubah. "
                        "Silakan review keranjang Anda untuk harga terbaru."
                    ),
                    "insufficient_stock": (
                        "Maaf, stok beberapa produk tidak mencukupi. "
                        "Silakan perbarui keranjang Anda."
                    ),
                }
                return {
                    "reply_text": error_messages.get(error_code, "Terjadi kesalahan."),
                    "pending_confirmation_token": None,
                }

            # Unknown error → escalate
            return {
                "escalation_flag": True,
                "escalation_reason": f"Order creation error: {error_code}",
            }

        # Success — order created
        order_id = result.get("order_id", "")
        total = result.get("total", "")
        currency = result.get("currency", "IDR")

        return {
            "last_order_id": order_id,
            "pending_confirmation_token": None,
        }

    return create_order


def create_order_edge(state: ConversationState) -> str:
    """Determine the next node after order creation."""
    if state.get("escalation_flag"):
        return "escalate"

    if state.get("last_order_id") and not state.get("reply_text"):
        # Order created successfully → generate payment link
        return "create_payment_link"

    # Validation error with reply_text set → send_reply
    return "send_reply"

"""Manage-cart node — handles cart add/update/remove operations.

Calls the appropriate cart service method based on the classified intent.
Tracks consecutive catalog failures and escalates after 2.

Requirements: Req 5
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class CartService(Protocol):
    """Protocol for the cart management dependency."""

    async def add_to_cart(
        self, customer_id: str, product_id: str, variant_id: str, quantity: int
    ) -> dict: ...

    async def update_cart_item_quantity(
        self, customer_id: str, cart_item_id: str, quantity: int
    ) -> dict: ...

    async def remove_from_cart(self, customer_id: str, cart_item_id: str) -> dict: ...


def build_manage_cart_node(
    cart_service: CartService,
) -> Any:
    """Build the manage_cart node function.

    Args:
        cart_service: A cart service (real or fake for testing).

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def manage_cart(state: ConversationState) -> dict[str, Any]:
        """Execute cart management operations based on intent."""
        intent = state.get("intent", "")
        customer_id = state.get("customer_id", "")
        consecutive_failures = state.get("consecutive_catalog_failures", 0)

        try:
            if intent == "cart_add":
                # Extract product info from search results or message context
                search_results = state.get("last_search_results", [])
                if search_results:
                    product = search_results[0]
                    result = await cart_service.add_to_cart(
                        customer_id=customer_id,
                        product_id=product.get("product_id", ""),
                        variant_id=product.get("variant_id", ""),
                        quantity=1,
                    )
                else:
                    return {
                        "reply_text": (
                            "Mohon pilih produk terlebih dahulu sebelum menambahkan ke keranjang."
                        ),
                        "consecutive_catalog_failures": 0,
                    }

            elif intent == "cart_update":
                # For update, we need cart_item_id and new quantity
                result = await cart_service.update_cart_item_quantity(
                    customer_id=customer_id,
                    cart_item_id=state.get("active_cart_id", ""),
                    quantity=1,
                )

            elif intent == "cart_remove":
                result = await cart_service.remove_from_cart(
                    customer_id=customer_id,
                    cart_item_id=state.get("active_cart_id", ""),
                )

            else:
                return {"reply_text": "Operasi keranjang tidak dikenali."}

        except Exception:
            new_failures = consecutive_failures + 1
            error_result: dict[str, Any] = {
                "consecutive_catalog_failures": new_failures,
            }

            if new_failures >= 2:
                error_result["escalation_flag"] = True
                error_result["escalation_reason"] = (
                    f"2 consecutive cart tool failures (total: {new_failures})"
                )
            else:
                error_result["reply_text"] = (
                    "Maaf, terjadi kendala saat memproses keranjang Anda. "
                    "Silakan coba lagi."
                )

            return error_result

        # Success — reset failures and compose reply
        reply = _format_cart_reply(result, intent)
        return {
            "reply_text": reply,
            "consecutive_catalog_failures": 0,
            "active_cart_id": result.get("cart_id", state.get("active_cart_id")),
        }

    return manage_cart


def manage_cart_edge(state: ConversationState) -> str:
    """Determine the next node after cart management."""
    if state.get("escalation_flag"):
        return "escalate"
    return "send_reply"


def _format_cart_reply(result: dict, intent: str) -> str:
    """Format a user-friendly cart reply from the tool result."""
    if result.get("ok") is False:
        error = result.get("error", {})
        return f"Maaf, terjadi masalah: {error.get('message', 'unknown error')}"

    if intent == "cart_add":
        return (
            f"Produk berhasil ditambahkan ke keranjang! "
            f"Subtotal: {result.get('subtotal', 'N/A')}"
        )
    elif intent == "cart_update":
        return f"Keranjang berhasil diperbarui. Subtotal: {result.get('subtotal', 'N/A')}"
    elif intent == "cart_remove":
        return f"Item berhasil dihapus dari keranjang. Subtotal: {result.get('subtotal', 'N/A')}"
    return "Keranjang berhasil diperbarui."

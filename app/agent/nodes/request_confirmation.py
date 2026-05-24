"""Request-confirmation node — computes customer_confirmation_token.

Generates a deterministic confirmation token from the cart snapshot and
presents the cart summary to the customer for explicit confirmation.

Requirements: Req 6
Design: LangGraph Agent Design
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from app.agent.state import ConversationState


class CartSnapshotService(Protocol):
    """Protocol for retrieving the current cart snapshot."""

    async def get_cart_snapshot(self, customer_id: str) -> dict | None: ...


def compute_confirmation_token(cart_snapshot: dict) -> str:
    """Compute a deterministic confirmation token from a cart snapshot.

    The token is derived from: cart_id + ordered list of cart_item_ids +
    quantities + unit_price_snapshots + currency.

    Args:
        cart_snapshot: Dict containing cart_id, items (list of dicts with
            cart_item_id, quantity, unit_price_snapshot), and currency.

    Returns:
        A hex digest string serving as the confirmation token.
    """
    # Build a canonical representation
    canonical = {
        "cart_id": cart_snapshot.get("cart_id", ""),
        "currency": cart_snapshot.get("currency", "IDR"),
        "items": sorted(
            [
                {
                    "cart_item_id": item.get("cart_item_id", ""),
                    "quantity": item.get("quantity", 0),
                    "unit_price_snapshot": str(item.get("unit_price_snapshot", "0")),
                }
                for item in cart_snapshot.get("items", [])
            ],
            key=lambda x: x["cart_item_id"],
        ),
    }

    token_input = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(token_input.encode()).hexdigest()


def build_request_confirmation_node(
    cart_snapshot_service: CartSnapshotService,
) -> Any:
    """Build the request_confirmation node function.

    Args:
        cart_snapshot_service: Service to retrieve cart snapshot.

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def request_confirmation(state: ConversationState) -> dict[str, Any]:
        """Compute confirmation token and present cart for confirmation."""
        customer_id = state.get("customer_id", "")

        try:
            cart_snapshot = await cart_snapshot_service.get_cart_snapshot(customer_id)
        except Exception:
            return {
                "reply_text": (
                    "Maaf, terjadi kendala saat mengambil data keranjang. "
                    "Silakan coba lagi."
                ),
            }

        if not cart_snapshot or not cart_snapshot.get("items"):
            return {
                "reply_text": "Keranjang Anda kosong. Silakan tambahkan produk terlebih dahulu.",
            }

        # Compute the confirmation token
        token = compute_confirmation_token(cart_snapshot)

        # Format the cart summary for the customer
        items = cart_snapshot.get("items", [])
        subtotal = cart_snapshot.get("subtotal", "0")
        currency = cart_snapshot.get("currency", "IDR")

        item_lines = []
        for item in items:
            name = item.get("product_name", "Produk")
            qty = item.get("quantity", 1)
            price = item.get("unit_price_snapshot", "0")
            item_lines.append(f"• {name} x{qty} @ {currency} {price}")

        summary = "\n".join(item_lines)
        reply = (
            f"Berikut ringkasan pesanan Anda:\n\n"
            f"{summary}\n\n"
            f"Total: {currency} {subtotal}\n\n"
            f"Apakah Anda ingin melanjutkan ke pembayaran? "
            f"Balas 'Ya' untuk konfirmasi."
        )

        return {
            "reply_text": reply,
            "pending_confirmation_token": token,
            "active_cart_id": cart_snapshot.get("cart_id"),
        }

    return request_confirmation

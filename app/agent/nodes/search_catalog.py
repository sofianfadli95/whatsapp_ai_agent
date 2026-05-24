"""Search-catalog node — searches the product catalog.

Calls the catalog search service/tool. Tracks consecutive failures
and escalates after 2 consecutive catalog tool failures.

Requirements: Reqs 3, 4
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class CatalogSearchService(Protocol):
    """Protocol for the catalog search dependency."""

    async def search(
        self,
        query: str,
        *,
        price_min: float | None = None,
        price_max: float | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]: ...


def build_search_catalog_node(
    catalog_service: CatalogSearchService,
) -> Any:
    """Build the search_catalog node function.

    Args:
        catalog_service: A catalog search service (real or fake for testing).

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def search_catalog(state: ConversationState) -> dict[str, Any]:
        """Search the product catalog based on the customer's query."""
        query = state.get("inbound_message_text", "") or ""
        consecutive_failures = state.get("consecutive_catalog_failures", 0)

        try:
            results = await catalog_service.search(query=query, limit=20)
        except Exception:
            new_failures = consecutive_failures + 1
            result: dict[str, Any] = {
                "last_search_results": [],
                "consecutive_catalog_failures": new_failures,
            }

            if new_failures >= 2:
                # 2nd consecutive failure → escalate
                result["escalation_flag"] = True
                result["escalation_reason"] = (
                    f"2 consecutive catalog tool failures (total: {new_failures})"
                )
            else:
                result["reply_text"] = (
                    "Maaf, pencarian produk sedang mengalami gangguan. "
                    "Silakan coba lagi atau saya bisa hubungkan dengan tim kami."
                )

            return result

        # Success — reset consecutive failures
        result_state: dict[str, Any] = {
            "last_search_results": results,
            "consecutive_catalog_failures": 0,
        }

        if not results:
            result_state["reply_text"] = (
                "Maaf, tidak ada produk yang cocok dengan pencarian Anda. "
                "Bisa coba dengan kata kunci lain atau saya hubungkan dengan tim kami?"
            )

        return result_state

    return search_catalog


def search_catalog_edge(state: ConversationState) -> str:
    """Determine the next node after catalog search."""
    if state.get("escalation_flag"):
        return "escalate"

    results = state.get("last_search_results", [])

    if not results:
        # Zero matches → send_reply with the "no matches" message
        return "send_reply"

    # Results found → recommend
    return "recommend"

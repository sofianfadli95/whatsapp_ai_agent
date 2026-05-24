"""Retrieve-RAG node — retrieves relevant product/FAQ context via RAG.

Queries the RAG retriever for relevant snippets. Tracks consecutive misses
and escalates after 2 consecutive RAG misses.

Requirements: Req 3
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol


from app.agent.state import ConversationState


class RAGRetriever(Protocol):
    """Protocol for the RAG retriever dependency."""

    async def retrieve(self, query: str, top_k: int, similarity_threshold: float) -> list[dict]: ...


def build_retrieve_rag_node(
    retriever: RAGRetriever,
    rag_top_k: int = 5,
    rag_similarity_threshold: float = 0.7,
) -> Any:
    """Build the retrieve_rag node function.

    Args:
        retriever: A RAG retriever (real or fake for testing).
        rag_top_k: Maximum number of results to retrieve.
        rag_similarity_threshold: Minimum similarity score.

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def retrieve_rag(state: ConversationState) -> dict[str, Any]:
        """Retrieve RAG snippets for the customer's question."""
        query = state.get("inbound_message_text", "") or ""
        consecutive_misses = state.get("consecutive_rag_misses", 0)

        try:
            snippets = await retriever.retrieve(
                query=query,
                top_k=rag_top_k,
                similarity_threshold=rag_similarity_threshold,
            )
        except Exception:
            # Treat retriever errors as a miss
            snippets = []

        if not snippets:
            new_misses = consecutive_misses + 1
            result: dict[str, Any] = {
                "rag_snippets": [],
                "consecutive_rag_misses": new_misses,
            }

            if new_misses >= 2:
                # 2nd consecutive miss → escalate
                result["escalation_flag"] = True
                result["escalation_reason"] = (
                    f"2 consecutive RAG misses (total: {new_misses})"
                )
            else:
                # First miss → ask clarifying question
                result["reply_text"] = (
                    "Maaf, saya belum menemukan informasi yang sesuai. "
                    "Bisa tolong jelaskan lebih detail apa yang Anda cari?"
                )

            return result

        # Successful retrieval — reset consecutive misses
        return {
            "rag_snippets": snippets,
            "consecutive_rag_misses": 0,
        }

    return retrieve_rag


def retrieve_rag_edge(state: ConversationState) -> str:
    """Determine the next node after RAG retrieval."""
    if state.get("escalation_flag"):
        return "escalate"

    snippets = state.get("rag_snippets", [])

    if not snippets:
        # No snippets but not escalated → clarifying question via send_reply
        return "send_reply"

    # Check if the intent suggests we need current price/stock
    intent = state.get("intent", "")
    if intent in ("search", "cart_add"):
        return "search_catalog"

    # Have grounded snippets → recommend
    return "recommend"

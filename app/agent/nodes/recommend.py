"""Recommend node — generates product recommendations from search/RAG results.

Uses the LLM to compose a recommendation reply based on grounded product data.
Always routes to send_reply.

Requirements: Req 4
Design: LangGraph Agent Design
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class RecommendationLLM(Protocol):
    """Protocol for the LLM callable that generates recommendations."""

    async def generate_recommendation(
        self, messages: list[dict], products: list[dict], snippets: list[dict]
    ) -> str: ...


class LangChainRecommender:
    """Recommendation generator backed by a LangChain BaseChatModel."""

    SYSTEM_PROMPT = (
        "You are a helpful WhatsApp sales assistant. Based on the product data provided, "
        "recommend up to 5 products to the customer. Include product name, price with currency, "
        "and at least one identifying attribute for each. Be concise and friendly. "
        "Use only the product data provided — do not invent any product information. "
        "Reply in Bahasa Indonesia."
    )

    def __init__(self, llm_factory: Any) -> None:
        self._llm_factory = llm_factory

    async def generate_recommendation(
        self, messages: list[dict], products: list[dict], snippets: list[dict]
    ) -> str:
        """Generate a recommendation reply using the LLM."""
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._llm_factory.get_chat_model(temperature=0.3)

        # Build context from products and snippets
        product_context = "\n".join(
            f"- {p.get('name', 'Unknown')}: {p.get('price', 'N/A')} ({p.get('variant', '')})"
            for p in products[:5]
        )
        snippet_context = "\n".join(
            s.get("content", "") for s in snippets[:3]
        )

        user_msg = (
            f"Product data:\n{product_context}\n\n"
            f"Additional context:\n{snippet_context}\n\n"
            f"Please recommend suitable products based on the customer's conversation."
        )

        prompt_messages = [
            SystemMessage(content=self.SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ]

        response = await llm.ainvoke(prompt_messages)
        return str(response.content)


def build_recommend_node(
    recommender: RecommendationLLM,
) -> Any:
    """Build the recommend node function.

    Args:
        recommender: A recommendation LLM (real or fake for testing).

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def recommend(state: ConversationState) -> dict[str, Any]:
        """Generate product recommendations from available data."""
        messages = state.get("messages", [])
        products = state.get("last_search_results", [])
        snippets = state.get("rag_snippets", [])

        try:
            reply = await recommender.generate_recommendation(
                messages=messages,
                products=products,
                snippets=snippets,
            )
        except Exception:
            reply = (
                "Maaf, saya sedang mengalami kendala dalam menyusun rekomendasi. "
                "Silakan coba lagi sebentar."
            )

        return {"reply_text": reply}

    return recommend

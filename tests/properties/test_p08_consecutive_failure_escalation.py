"""Property-based tests for consecutive failure escalation (Property 8).

**Validates: Requirements 3.6, 3.11, 10.1, 10.2**

Property 8: For any conversation, for any sequence of agent turns that contains
two consecutive RAG-misses below the similarity threshold OR two consecutive
Catalog-tool failures/timeouts, the Escalation_Flag SHALL be set on the
conversation after the second occurrence and no further automated reply SHALL
be produced. The same property SHALL hold whenever the agent's intent classifier
identifies the customer message as a request for human support,
refund/cancellation, or a payment problem report, OR whenever the
candidate-reply confidence falls below ESCALATION_CONFIDENCE_THRESHOLD.

Tests exercise the retrieve_rag, search_catalog, and route_intent nodes
directly using fake implementations controlled by Hypothesis strategies.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.agent.nodes.retrieve_rag import build_retrieve_rag_node
from app.agent.nodes.search_catalog import build_search_catalog_node
from app.agent.nodes.route_intent import (
    IntentClassification,
    build_route_intent_node,
)
from app.agent.state import ConversationState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class EmptyRAGRetriever:
    """A RAG retriever that always returns empty results (simulates miss)."""

    async def retrieve(
        self, query: str, top_k: int, similarity_threshold: float
    ) -> list[dict]:
        return []


class FailingCatalogService:
    """A catalog service that always raises an exception (simulates failure)."""

    async def search(
        self,
        query: str,
        *,
        price_min: float | None = None,
        price_max: float | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        raise RuntimeError("Catalog service unavailable")


class TimeoutCatalogService:
    """A catalog service that raises a timeout error."""

    async def search(
        self,
        query: str,
        *,
        price_min: float | None = None,
        price_max: float | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        raise TimeoutError("Catalog search timed out")


class FakeIntentClassifier:
    """A fake intent classifier that returns a pre-configured classification."""

    def __init__(self, intent: str, confidence: float) -> None:
        self._intent = intent
        self._confidence = confidence

    async def classify(self, messages: list[dict]) -> IntentClassification:
        return IntentClassification(intent=self._intent, confidence=self._confidence)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

query_st = st.text(min_size=1, max_size=200)
rag_top_k_st = st.integers(min_value=1, max_value=20)
rag_threshold_st = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

# Escalation confidence threshold
escalation_threshold_st = st.floats(min_value=0.1, max_value=0.9, allow_nan=False)

# Confidence below threshold
low_confidence_st = st.floats(min_value=0.0, max_value=0.99, allow_nan=False)

# Intents that trigger escalation
escalation_intent_st = st.sampled_from(["support_request", "complaint"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_state(
    query: str = "test query",
    consecutive_rag_misses: int = 0,
    consecutive_catalog_failures: int = 0,
    escalation_flag: bool = False,
    messages: list[dict] | None = None,
) -> ConversationState:
    """Create a minimal ConversationState for testing."""
    return ConversationState(
        conversation_id="test-conv-001",
        customer_id="test-customer-001",
        phone_e164="+6281234567890",
        inbound_message_text=query,
        inbound_message_type="text",
        messages=messages or [{"role": "user", "content": query}],
        rag_snippets=[],
        last_search_results=[],
        consecutive_rag_misses=consecutive_rag_misses,
        consecutive_catalog_failures=consecutive_catalog_failures,
        escalation_flag=escalation_flag,
    )


# ---------------------------------------------------------------------------
# Property Tests: Two consecutive RAG misses → escalation
# ---------------------------------------------------------------------------


@given(query=query_st, top_k=rag_top_k_st, threshold=rag_threshold_st)
@settings(max_examples=200)
def test_second_consecutive_rag_miss_sets_escalation_flag(
    query: str, top_k: int, threshold: float
) -> None:
    """Property: 2nd consecutive RAG miss sets escalation_flag.

    **Validates: Requirements 3.6**

    For any conversation with one prior consecutive RAG miss, a second
    consecutive miss SHALL set the escalation_flag on the conversation.
    """
    retriever = EmptyRAGRetriever()
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    # State already has 1 consecutive miss
    state = _make_state(query=query, consecutive_rag_misses=1)
    result = _run(node(state))

    assert result.get("escalation_flag") is True, (
        "Expected escalation_flag=True after 2nd consecutive RAG miss"
    )
    assert result.get("consecutive_rag_misses") == 2, (
        f"Expected consecutive_rag_misses=2, got {result.get('consecutive_rag_misses')}"
    )


@given(query=query_st, top_k=rag_top_k_st, threshold=rag_threshold_st)
@settings(max_examples=200)
def test_first_rag_miss_does_not_escalate(
    query: str, top_k: int, threshold: float
) -> None:
    """Property: 1st RAG miss does NOT set escalation_flag.

    **Validates: Requirements 3.6**

    A single RAG miss SHALL NOT trigger escalation; it should produce
    a clarifying question instead.
    """
    retriever = EmptyRAGRetriever()
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    state = _make_state(query=query, consecutive_rag_misses=0)
    result = _run(node(state))

    assert result.get("escalation_flag") is not True, (
        "Should NOT escalate on first RAG miss"
    )
    assert result.get("consecutive_rag_misses") == 1, (
        f"Expected consecutive_rag_misses=1, got {result.get('consecutive_rag_misses')}"
    )
    # Should have a clarifying question
    assert result.get("reply_text") is not None, (
        "Expected a clarifying question on first RAG miss"
    )


# ---------------------------------------------------------------------------
# Property Tests: Two consecutive catalog failures → escalation
# ---------------------------------------------------------------------------


@given(query=query_st)
@settings(max_examples=200)
def test_second_consecutive_catalog_failure_sets_escalation_flag(
    query: str,
) -> None:
    """Property: 2nd consecutive catalog failure sets escalation_flag.

    **Validates: Requirements 3.11**

    For any conversation with one prior consecutive catalog failure, a second
    consecutive failure SHALL set the escalation_flag on the conversation.
    """
    catalog_service = FailingCatalogService()
    node = build_search_catalog_node(catalog_service=catalog_service)

    # State already has 1 consecutive failure
    state = _make_state(query=query, consecutive_catalog_failures=1)
    result = _run(node(state))

    assert result.get("escalation_flag") is True, (
        "Expected escalation_flag=True after 2nd consecutive catalog failure"
    )
    assert result.get("consecutive_catalog_failures") == 2, (
        f"Expected consecutive_catalog_failures=2, "
        f"got {result.get('consecutive_catalog_failures')}"
    )


@given(query=query_st)
@settings(max_examples=200)
def test_first_catalog_failure_does_not_escalate(
    query: str,
) -> None:
    """Property: 1st catalog failure does NOT set escalation_flag.

    **Validates: Requirements 3.11**

    A single catalog failure SHALL NOT trigger escalation.
    """
    catalog_service = FailingCatalogService()
    node = build_search_catalog_node(catalog_service=catalog_service)

    state = _make_state(query=query, consecutive_catalog_failures=0)
    result = _run(node(state))

    assert result.get("escalation_flag") is not True, (
        "Should NOT escalate on first catalog failure"
    )
    assert result.get("consecutive_catalog_failures") == 1, (
        f"Expected consecutive_catalog_failures=1, "
        f"got {result.get('consecutive_catalog_failures')}"
    )


@given(query=query_st)
@settings(max_examples=200)
def test_catalog_timeout_counts_as_failure(
    query: str,
) -> None:
    """Property: catalog timeout is treated as a failure for escalation counting.

    **Validates: Requirements 3.11**

    A timeout on the catalog service SHALL be counted as a consecutive failure.
    """
    catalog_service = TimeoutCatalogService()
    node = build_search_catalog_node(catalog_service=catalog_service)

    state = _make_state(query=query, consecutive_catalog_failures=1)
    result = _run(node(state))

    assert result.get("escalation_flag") is True, (
        "Expected escalation_flag=True after 2nd consecutive catalog timeout"
    )
    assert result.get("consecutive_catalog_failures") == 2, (
        f"Expected consecutive_catalog_failures=2, "
        f"got {result.get('consecutive_catalog_failures')}"
    )


# ---------------------------------------------------------------------------
# Property Tests: Low confidence → escalation
# ---------------------------------------------------------------------------


@given(
    query=query_st,
    threshold=escalation_threshold_st,
    confidence=low_confidence_st,
)
@settings(max_examples=200)
def test_low_confidence_below_threshold_sets_escalation_flag(
    query: str, threshold: float, confidence: float
) -> None:
    """Property: confidence below ESCALATION_CONFIDENCE_THRESHOLD → escalation.

    **Validates: Requirements 10.2**

    Whenever the candidate-reply confidence falls below
    ESCALATION_CONFIDENCE_THRESHOLD, the Escalation_Flag SHALL be set.
    """
    # Ensure confidence is strictly below threshold
    if confidence >= threshold:
        confidence = threshold - 0.01
    if confidence < 0.0:
        confidence = 0.0
        threshold = 0.1  # Ensure threshold > confidence

    classifier = FakeIntentClassifier(intent="inquiry", confidence=confidence)
    node = build_route_intent_node(
        classifier=classifier,
        escalation_confidence_threshold=threshold,
    )

    state = _make_state(query=query)
    result = _run(node(state))

    assert result.get("escalation_flag") is True, (
        f"Expected escalation_flag=True when confidence={confidence} "
        f"< threshold={threshold}"
    )


@given(
    query=query_st,
    intent=escalation_intent_st,
)
@settings(max_examples=200)
def test_escalation_intents_route_to_escalate(
    query: str, intent: str
) -> None:
    """Property: support_request/complaint intents route to escalation.

    **Validates: Requirements 10.1**

    Whenever the intent classifier identifies the customer message as a
    request for human support or complaint, the routing SHALL direct to
    escalation. This is tested via the route_intent_edge function.
    """
    from app.agent.nodes.route_intent import route_intent_edge

    # Simulate state after route_intent node has classified intent
    state = _make_state(query=query)
    # Manually set the intent (as if route_intent node already ran)
    state_with_intent: dict[str, Any] = dict(state)
    state_with_intent["intent"] = intent
    state_with_intent["intent_confidence"] = 0.95
    state_with_intent["escalation_flag"] = False

    # For support_request and complaint, the edge function routes to "escalate"
    next_node = route_intent_edge(state_with_intent)  # type: ignore[arg-type]
    assert next_node == "escalate", (
        f"Expected route to 'escalate' for intent={intent}, got '{next_node}'"
    )

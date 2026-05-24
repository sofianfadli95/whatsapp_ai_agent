"""Property-based tests for RAG retrieval bounds (Property 7).

**Validates: Requirements 3.3, 3.5**

Property 7: For any query string and for any configured RAG_TOP_K ∈ [1, 20]
and RAG_SIMILARITY_THRESHOLD ∈ [0.0, 1.0], RAGRetriever.retrieve(query) SHALL
return a list of length ≤ RAG_TOP_K whose every element has similarity ≥
RAG_SIMILARITY_THRESHOLD. For any query yielding zero results above the
threshold, the agent's reply SHALL be a single clarifying question and the
consecutive-RAG-miss counter for that conversation SHALL be incremented.

Tests exercise the retrieve_rag node directly using fake RAGRetriever
implementations controlled by Hypothesis strategies.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.agent.nodes.retrieve_rag import build_retrieve_rag_node
from app.agent.state import ConversationState


# ---------------------------------------------------------------------------
# Fake RAGRetriever that returns controlled results
# ---------------------------------------------------------------------------


class FakeRAGRetriever:
    """A fake RAGRetriever that returns pre-configured snippets.

    The retriever respects top_k and similarity_threshold parameters,
    filtering and limiting results just like a real implementation would.
    """

    def __init__(self, snippets: list[dict]) -> None:
        """Initialize with a list of snippet dicts, each having a 'similarity' key."""
        self._snippets = snippets

    async def retrieve(
        self, query: str, top_k: int, similarity_threshold: float
    ) -> list[dict]:
        """Return snippets filtered by threshold and limited by top_k."""
        filtered = [
            s for s in self._snippets if s.get("similarity", 0.0) >= similarity_threshold
        ]
        # Sort by similarity descending (most relevant first)
        filtered.sort(key=lambda s: s.get("similarity", 0.0), reverse=True)
        return filtered[:top_k]


class FailingRAGRetriever:
    """A fake RAGRetriever that always raises an exception."""

    async def retrieve(
        self, query: str, top_k: int, similarity_threshold: float
    ) -> list[dict]:
        raise RuntimeError("RAG retriever unavailable")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Query strings: non-empty text
query_st = st.text(min_size=1, max_size=200)

# RAG_TOP_K: integer in [1, 20]
rag_top_k_st = st.integers(min_value=1, max_value=20)

# RAG_SIMILARITY_THRESHOLD: float in [0.0, 1.0]
rag_threshold_st = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

# A single snippet with a similarity score
snippet_st = st.fixed_dictionaries(
    {
        "content": st.text(min_size=1, max_size=100),
        "similarity": st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        "source": st.text(min_size=1, max_size=50),
    }
)

# A list of snippets (0 to 30 to exceed top_k sometimes)
snippets_list_st = st.lists(snippet_st, min_size=0, max_size=30)

# Consecutive RAG misses counter (starting state)
consecutive_misses_st = st.integers(min_value=0, max_value=5)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_state(query: str, consecutive_rag_misses: int = 0) -> ConversationState:
    """Create a minimal ConversationState for testing the retrieve_rag node."""
    return ConversationState(
        conversation_id="test-conv-001",
        customer_id="test-customer-001",
        phone_e164="+6281234567890",
        inbound_message_text=query,
        inbound_message_type="text",
        messages=[],
        rag_snippets=[],
        consecutive_rag_misses=consecutive_rag_misses,
        escalation_flag=False,
    )


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(
    query=query_st,
    top_k=rag_top_k_st,
    threshold=rag_threshold_st,
    snippets=snippets_list_st,
)
@settings(max_examples=200)
def test_rag_results_bounded_by_top_k(
    query: str, top_k: int, threshold: float, snippets: list[dict]
) -> None:
    """Property: RAG retrieval returns at most RAG_TOP_K results.

    **Validates: Requirements 3.3**

    For any query and any configured RAG_TOP_K ∈ [1, 20], the retrieve_rag
    node SHALL produce rag_snippets with length ≤ RAG_TOP_K.
    """
    retriever = FakeRAGRetriever(snippets)
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    state = _make_state(query)
    result = _run(node(state))

    returned_snippets = result.get("rag_snippets", [])
    assert len(returned_snippets) <= top_k, (
        f"RAG returned {len(returned_snippets)} snippets, exceeding top_k={top_k}"
    )


@given(
    query=query_st,
    top_k=rag_top_k_st,
    threshold=rag_threshold_st,
    snippets=snippets_list_st,
)
@settings(max_examples=200)
def test_rag_results_meet_similarity_threshold(
    query: str, top_k: int, threshold: float, snippets: list[dict]
) -> None:
    """Property: every RAG result has similarity ≥ RAG_SIMILARITY_THRESHOLD.

    **Validates: Requirements 3.3**

    For any query and any configured RAG_SIMILARITY_THRESHOLD ∈ [0.0, 1.0],
    every element in the returned rag_snippets SHALL have similarity ≥ threshold.
    """
    retriever = FakeRAGRetriever(snippets)
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    state = _make_state(query)
    result = _run(node(state))

    returned_snippets = result.get("rag_snippets", [])
    for snippet in returned_snippets:
        assert snippet.get("similarity", 0.0) >= threshold, (
            f"Snippet similarity {snippet.get('similarity')} is below "
            f"threshold {threshold}"
        )


@given(
    query=query_st,
    top_k=rag_top_k_st,
    threshold=rag_threshold_st,
    snippets=snippets_list_st,
    consecutive_misses=consecutive_misses_st,
)
@settings(max_examples=200)
def test_zero_results_produces_clarifying_question_and_increments_counter(
    query: str,
    top_k: int,
    threshold: float,
    snippets: list[dict],
    consecutive_misses: int,
) -> None:
    """Property: zero results above threshold → clarifying question + counter increment.

    **Validates: Requirements 3.5**

    For any query yielding zero results above the threshold, the agent's reply
    SHALL be a single clarifying question and the consecutive-RAG-miss counter
    for that conversation SHALL be incremented.
    """
    # Ensure all snippets are below threshold so we get zero results
    below_threshold_snippets = [
        {**s, "similarity": min(s["similarity"], threshold - 0.01)}
        for s in snippets
    ]
    # If threshold is 0.0, any snippet with similarity >= 0.0 would pass,
    # so we need to ensure empty results by using no snippets
    if threshold <= 0.0:
        below_threshold_snippets = []

    retriever = FakeRAGRetriever(below_threshold_snippets)
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    state = _make_state(query, consecutive_rag_misses=consecutive_misses)
    result = _run(node(state))

    # Should have zero snippets
    assert result.get("rag_snippets") == [], (
        f"Expected empty rag_snippets but got {result.get('rag_snippets')}"
    )

    # Counter should be incremented
    assert result.get("consecutive_rag_misses") == consecutive_misses + 1, (
        f"Expected consecutive_rag_misses={consecutive_misses + 1}, "
        f"got {result.get('consecutive_rag_misses')}"
    )

    # If this is the first miss (not yet at 2), should have a clarifying question
    if consecutive_misses < 1:
        assert result.get("reply_text") is not None, (
            "Expected a clarifying question reply_text on first RAG miss"
        )
        # Should NOT set escalation flag on first miss
        assert result.get("escalation_flag") is not True, (
            "Should not escalate on first RAG miss"
        )


@given(
    query=query_st,
    top_k=rag_top_k_st,
    threshold=rag_threshold_st,
)
@settings(max_examples=200)
def test_retriever_exception_treated_as_miss(
    query: str, top_k: int, threshold: float
) -> None:
    """Property: retriever exceptions are treated as a RAG miss.

    **Validates: Requirements 3.3, 3.5**

    When the RAG retriever raises an exception, the node SHALL treat it
    as zero results (a miss), incrementing the consecutive miss counter.
    """
    retriever = FailingRAGRetriever()
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    state = _make_state(query, consecutive_rag_misses=0)
    result = _run(node(state))

    assert result.get("rag_snippets") == [], (
        "Expected empty rag_snippets on retriever exception"
    )
    assert result.get("consecutive_rag_misses") == 1, (
        "Expected consecutive_rag_misses=1 on retriever exception"
    )


@given(
    query=query_st,
    top_k=rag_top_k_st,
    threshold=rag_threshold_st,
    snippets=snippets_list_st,
)
@settings(max_examples=200)
def test_successful_retrieval_resets_miss_counter(
    query: str, top_k: int, threshold: float, snippets: list[dict]
) -> None:
    """Property: successful retrieval resets the consecutive miss counter to 0.

    **Validates: Requirements 3.3**

    When the retriever returns at least one result above the threshold,
    the consecutive_rag_misses counter SHALL be reset to 0.
    """
    # Ensure at least one snippet is above threshold
    above_threshold_snippets = snippets + [
        {"content": "guaranteed hit", "similarity": min(1.0, threshold + 0.1), "source": "test"}
    ]
    assume(threshold < 1.0)  # Otherwise even threshold + 0.1 won't help

    retriever = FakeRAGRetriever(above_threshold_snippets)
    node = build_retrieve_rag_node(
        retriever=retriever,
        rag_top_k=top_k,
        rag_similarity_threshold=threshold,
    )

    state = _make_state(query, consecutive_rag_misses=3)
    result = _run(node(state))

    # Should have results
    assert len(result.get("rag_snippets", [])) > 0, (
        "Expected non-empty rag_snippets with a guaranteed above-threshold snippet"
    )
    # Counter should be reset
    assert result.get("consecutive_rag_misses") == 0, (
        f"Expected consecutive_rag_misses=0 after successful retrieval, "
        f"got {result.get('consecutive_rag_misses')}"
    )

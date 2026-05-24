"""Tests for app.agent.state — ConversationState TypedDict.

Verifies that the state can be instantiated and serialized to JSON,
confirming checkpointing compatibility.
"""

import json

from app.agent.state import ConversationState, ToolErrorRecord


def test_empty_state_serializes_to_json() -> None:
    """An empty ConversationState (all optional fields omitted) serializes to JSON."""
    state: ConversationState = {}  # type: ignore[typeddict-item]
    result = json.dumps(state)
    assert result == "{}"
    assert json.loads(result) == {}


def test_fully_populated_state_serializes_to_json() -> None:
    """A fully populated ConversationState round-trips through JSON."""
    error: ToolErrorRecord = {
        "code": "provider_unavailable",
        "message": "Timeout after 10s",
        "tool": "create_payment_link",
        "occurred_at": "2024-01-15T10:30:00Z",
    }

    state: ConversationState = {
        "conversation_id": "conv-123",
        "customer_id": "cust-456",
        "phone_e164": "+6281234567890",
        "messages": [{"role": "human", "content": "Halo"}],
        "inbound_message_id": "msg-789",
        "inbound_message_text": "Halo",
        "inbound_message_type": "text",
        "intent": "inquiry",
        "intent_confidence": 0.92,
        "rag_snippets": [{"text": "Product A is great", "score": 0.85}],
        "last_search_results": [{"product_id": "prod-1", "name": "Product A"}],
        "active_cart_id": "cart-001",
        "pending_confirmation_token": "tok-abc",
        "last_order_id": "ord-100",
        "last_payment_link": "https://pay.example.com/link/xyz",
        "escalation_flag": False,
        "escalation_reason": None,
        "consecutive_rag_misses": 0,
        "consecutive_catalog_failures": 0,
        "tool_errors": [error],
        "reply_text": "Halo! Ada yang bisa saya bantu?",
        "reply_enqueued": False,
        "request_id": "req-aaa",
        "turn_started_at": "2024-01-15T10:30:00Z",
    }

    serialized = json.dumps(state)
    deserialized = json.loads(serialized)

    assert deserialized["conversation_id"] == "conv-123"
    assert deserialized["phone_e164"] == "+6281234567890"
    assert deserialized["intent_confidence"] == 0.92
    assert deserialized["escalation_flag"] is False
    assert len(deserialized["tool_errors"]) == 1
    assert deserialized["tool_errors"][0]["code"] == "provider_unavailable"
    assert deserialized["reply_enqueued"] is False


def test_partial_state_serializes_to_json() -> None:
    """A partial state (only identity fields) serializes correctly."""
    state: ConversationState = {
        "conversation_id": "conv-999",
        "customer_id": "cust-111",
        "phone_e164": "+628999888777",
    }

    serialized = json.dumps(state)
    deserialized = json.loads(serialized)

    assert deserialized == {
        "conversation_id": "conv-999",
        "customer_id": "cust-111",
        "phone_e164": "+628999888777",
    }

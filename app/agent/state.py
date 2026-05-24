"""ConversationState TypedDict for the LangGraph agent.

This module defines the full state shape used by the LangGraph conversation
graph. The state is checkpointed to PostgreSQL via PostgresSaver.

Requirements: Req 2 (Conversation state + checkpointing), Req 10 (Human escalation).
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Literal, Optional, TypedDict


class ToolErrorRecord(TypedDict):
    """A structured record of a tool error observed during a turn."""

    code: str
    message: str
    tool: str
    occurred_at: str  # ISO 8601 UTC


class ConversationState(TypedDict, total=False):
    """Full LangGraph state shape for a single conversation thread.

    Fields use ``total=False`` so partial updates can be applied per-node.
    The ``messages`` field uses the LangGraph reducer pattern (``Annotated[list, add]``)
    so each node can append without replacing the full list.
    """

    # Identity
    conversation_id: str
    customer_id: str
    phone_e164: str

    # Message thread (serialized BaseMessage dicts; reducer appends new entries)
    messages: Annotated[list[dict], add]  # type: ignore[type-arg]

    # Latest inbound
    inbound_message_id: str
    inbound_message_text: Optional[str]
    inbound_message_type: Literal[
        "text", "image", "audio", "video", "document", "sticker", "location"
    ]

    # Routing / classification
    intent: Optional[str]
    intent_confidence: Optional[float]

    # RAG / catalog working memory (NOT source of truth)
    rag_snippets: list[dict]  # type: ignore[type-arg]
    last_search_results: list[dict]  # type: ignore[type-arg]

    # Cart / Order working references (IDs only; values come from tools)
    active_cart_id: Optional[str]
    pending_confirmation_token: Optional[str]
    last_order_id: Optional[str]
    last_payment_link: Optional[str]

    # Escalation tracking
    escalation_flag: bool
    escalation_reason: Optional[str]
    consecutive_rag_misses: int
    consecutive_catalog_failures: int

    # Errors observed during this turn (for audit + retry decisions)
    tool_errors: list[ToolErrorRecord]

    # Reply to be sent
    reply_text: Optional[str]
    reply_enqueued: bool

    # Tracing
    request_id: str
    turn_started_at: str

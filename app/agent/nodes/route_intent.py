"""Route-intent node — LLM-based structured-output intent classifier.

Uses the LLM with a structured output parser to classify the customer's
latest message into an intent category with a confidence score.

Requirements: Reqs 3, 4, 5, 6, 7, 10
Design: LangGraph Agent Design, Properties 7, 8, 24
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.agent.state import ConversationState
from app.config import Settings


# --- Structured output schema for intent classification ---

VALID_INTENTS = [
    "inquiry",
    "search",
    "cart_add",
    "cart_update",
    "cart_remove",
    "checkout_request",
    "customer_confirmed",
    "payment_question",
    "support_request",
    "complaint",
    "non_text",
]


class IntentClassification(BaseModel):
    """Structured output from the intent classifier LLM call."""

    intent: str = Field(description="The classified intent of the customer message.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score for the classification."
    )


# --- Protocol for the LLM callable used by this node ---


class IntentClassifierLLM(Protocol):
    """Protocol for the LLM callable that classifies intent."""

    async def classify(self, messages: list[dict]) -> IntentClassification: ...


# --- Default implementation using LangChain structured output ---


class LangChainIntentClassifier:
    """Intent classifier backed by a LangChain BaseChatModel with structured output."""

    SYSTEM_PROMPT = (
        "You are an intent classifier for a WhatsApp sales agent. "
        "Classify the customer's latest message into exactly one of these intents: "
        f"{', '.join(VALID_INTENTS)}. "
        "Return your classification with a confidence score between 0 and 1."
    )

    def __init__(self, llm_factory: Any, settings: Settings) -> None:
        self._llm_factory = llm_factory
        self._settings = settings

    async def classify(self, messages: list[dict]) -> IntentClassification:
        """Classify intent using the LLM with structured output."""
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._llm_factory.get_chat_model(temperature=0.0)
        structured_llm = llm.with_structured_output(IntentClassification)

        # Build the prompt: system + conversation context
        prompt_messages = [SystemMessage(content=self.SYSTEM_PROMPT)]
        for msg in messages[-5:]:  # Last 5 messages for context
            if msg.get("role") == "user" or msg.get("type") == "human":
                prompt_messages.append(HumanMessage(content=msg.get("content", "")))

        result = await structured_llm.ainvoke(prompt_messages)
        if isinstance(result, IntentClassification):
            return result
        # Fallback if structured output returns a dict
        return IntentClassification(**result)  # type: ignore[arg-type]


# --- Node function ---


def build_route_intent_node(
    classifier: IntentClassifierLLM,
    escalation_confidence_threshold: float = 0.6,
) -> Any:
    """Build the route_intent node function.

    Args:
        classifier: An intent classifier (LLM or fake for testing).
        escalation_confidence_threshold: Threshold below which we escalate.

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def route_intent(state: ConversationState) -> dict[str, Any]:
        """Classify intent and update state with routing information."""
        messages = state.get("messages", [])
        inbound_type = state.get("inbound_message_type", "text")

        # Non-text messages get classified as non_text directly
        if inbound_type != "text":
            return {
                "intent": "non_text",
                "intent_confidence": 1.0,
            }

        # If no messages, default to inquiry
        if not messages:
            return {
                "intent": "inquiry",
                "intent_confidence": 0.5,
            }

        classification = await classifier.classify(messages)

        result: dict[str, Any] = {
            "intent": classification.intent,
            "intent_confidence": classification.confidence,
        }

        # If confidence is below threshold, set escalation
        if classification.confidence < escalation_confidence_threshold:
            result["escalation_flag"] = True
            result["escalation_reason"] = (
                f"Low confidence ({classification.confidence:.2f}) "
                f"below threshold ({escalation_confidence_threshold})"
            )
            # Suppress the candidate reply
            result["reply_text"] = None

        return result

    return route_intent


def route_intent_edge(state: ConversationState) -> str:
    """Determine the next node based on intent classification.

    Returns the name of the next node to route to.
    """
    # If escalation flag is set (low confidence or explicit escalation intents)
    if state.get("escalation_flag"):
        return "escalate"

    intent = state.get("intent", "inquiry")

    # Map intents to next nodes
    intent_routing: dict[str, str] = {
        "inquiry": "retrieve_rag",
        "search": "search_catalog",
        "cart_add": "manage_cart",
        "cart_update": "manage_cart",
        "cart_remove": "manage_cart",
        "checkout_request": "request_confirmation",
        "customer_confirmed": "create_order",
        "payment_question": "retrieve_rag",
        "support_request": "escalate",
        "complaint": "escalate",
        "non_text": "send_reply",
    }

    return intent_routing.get(intent, "retrieve_rag")

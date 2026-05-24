"""Unit tests for LangGraph agent nodes.

Uses FakeChatModel and fake service implementations to exercise each
branching condition and assert the resulting ConversationState changes.

Validates: Requirements 3, 4, 5, 6, 7, 10
Design: LangGraph Agent Design, Properties 7, 8, 24
"""

from __future__ import annotations

import pytest

from app.agent.nodes.route_intent import (
    IntentClassification,
    build_route_intent_node,
    route_intent_edge,
)
from app.agent.nodes.retrieve_rag import build_retrieve_rag_node, retrieve_rag_edge
from app.agent.nodes.search_catalog import build_search_catalog_node, search_catalog_edge
from app.agent.nodes.recommend import build_recommend_node
from app.agent.nodes.manage_cart import build_manage_cart_node, manage_cart_edge
from app.agent.nodes.request_confirmation import (
    build_request_confirmation_node,
    compute_confirmation_token,
)
from app.agent.nodes.create_order import build_create_order_node, create_order_edge
from app.agent.nodes.create_payment_link import (
    build_create_payment_link_node,
    create_payment_link_edge,
)
from app.agent.nodes.escalate import build_escalate_node
from app.agent.nodes.send_reply import build_send_reply_node


# =============================================================================
# Fake / Mock implementations
# =============================================================================


class FakeIntentClassifier:
    """Fake intent classifier that returns predetermined results."""

    def __init__(self, intent: str = "inquiry", confidence: float = 0.9):
        self.intent = intent
        self.confidence = confidence

    async def classify(self, messages: list[dict]) -> IntentClassification:
        return IntentClassification(intent=self.intent, confidence=self.confidence)


class FakeRAGRetriever:
    """Fake RAG retriever that returns predetermined results."""

    def __init__(self, results: list[dict] | None = None, should_fail: bool = False):
        self.results = results if results is not None else []
        self.should_fail = should_fail

    async def retrieve(self, query: str, top_k: int, similarity_threshold: float) -> list[dict]:
        if self.should_fail:
            raise RuntimeError("RAG retriever failed")
        return self.results


class FakeCatalogSearchService:
    """Fake catalog search service."""

    def __init__(self, results: list[dict] | None = None, should_fail: bool = False):
        self.results = results if results is not None else []
        self.should_fail = should_fail

    async def search(
        self,
        query: str,
        *,
        price_min: float | None = None,
        price_max: float | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if self.should_fail:
            raise RuntimeError("Catalog search failed")
        return self.results


class FakeRecommender:
    """Fake recommendation LLM."""

    def __init__(self, reply: str = "Here are my recommendations!", should_fail: bool = False):
        self.reply = reply
        self.should_fail = should_fail

    async def generate_recommendation(
        self, messages: list[dict], products: list[dict], snippets: list[dict]
    ) -> str:
        if self.should_fail:
            raise RuntimeError("Recommender failed")
        return self.reply


class FakeCartService:
    """Fake cart service."""

    def __init__(self, result: dict | None = None, should_fail: bool = False):
        self.result = result or {"ok": True, "cart_id": "cart-1", "subtotal": "150000"}
        self.should_fail = should_fail

    async def add_to_cart(
        self, customer_id: str, product_id: str, variant_id: str, quantity: int
    ) -> dict:
        if self.should_fail:
            raise RuntimeError("Cart service failed")
        return self.result

    async def update_cart_item_quantity(
        self, customer_id: str, cart_item_id: str, quantity: int
    ) -> dict:
        if self.should_fail:
            raise RuntimeError("Cart service failed")
        return self.result

    async def remove_from_cart(self, customer_id: str, cart_item_id: str) -> dict:
        if self.should_fail:
            raise RuntimeError("Cart service failed")
        return self.result


class FakeCartSnapshotService:
    """Fake cart snapshot service."""

    def __init__(self, snapshot: dict | None = None, should_fail: bool = False):
        self.snapshot = snapshot
        self.should_fail = should_fail

    async def get_cart_snapshot(self, customer_id: str) -> dict | None:
        if self.should_fail:
            raise RuntimeError("Cart snapshot failed")
        return self.snapshot


class FakeOrderService:
    """Fake order service."""

    def __init__(self, result: dict | None = None, should_fail: bool = False):
        self.result = result or {
            "ok": True,
            "order_id": "order-123",
            "total": "150000",
            "currency": "IDR",
        }
        self.should_fail = should_fail

    async def create_order(
        self, customer_id: str, cart_id: str, customer_confirmation_token: str
    ) -> dict:
        if self.should_fail:
            raise RuntimeError("Order service failed")
        return self.result


class FakePaymentService:
    """Fake payment service."""

    def __init__(self, result: dict | None = None, should_fail: bool = False):
        self.result = result or {
            "ok": True,
            "link_url": "https://pay.example.com/abc123",
        }
        self.should_fail = should_fail

    async def create_payment_link(self, order_id: str) -> dict:
        if self.should_fail:
            raise RuntimeError("Payment service failed")
        return self.result


class FakeEscalationService:
    """Fake escalation service."""

    def __init__(self, should_fail: bool = False):
        self.escalated: list[dict] = []
        self.should_fail = should_fail

    async def escalate(self, conversation_id: str, reason: str) -> dict:
        if self.should_fail:
            raise RuntimeError("Escalation service failed")
        self.escalated.append({"conversation_id": conversation_id, "reason": reason})
        return {"ok": True}


class FakeSendQueue:
    """Fake send queue that captures enqueued items."""

    def __init__(self):
        self.items: list = []

    async def put(self, item) -> None:
        self.items.append(item)


# =============================================================================
# Tests: route_intent node
# =============================================================================


class TestRouteIntentNode:
    """Tests for the route_intent node."""

    @pytest.mark.asyncio
    async def test_classifies_intent_from_messages(self):
        """Node should classify intent and set state fields."""
        classifier = FakeIntentClassifier(intent="search", confidence=0.95)
        node = build_route_intent_node(classifier, escalation_confidence_threshold=0.6)

        state = {"messages": [{"role": "user", "content": "cari moisturizer"}]}
        result = await node(state)

        assert result["intent"] == "search"
        assert result["intent_confidence"] == 0.95
        assert "escalation_flag" not in result

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_escalation(self):
        """Low confidence below threshold should set escalation flag."""
        classifier = FakeIntentClassifier(intent="inquiry", confidence=0.3)
        node = build_route_intent_node(classifier, escalation_confidence_threshold=0.6)

        state = {"messages": [{"role": "user", "content": "hmm"}]}
        result = await node(state)

        assert result["escalation_flag"] is True
        assert "Low confidence" in result["escalation_reason"]
        assert result["reply_text"] is None  # Candidate reply suppressed

    @pytest.mark.asyncio
    async def test_non_text_message_classified_directly(self):
        """Non-text messages should be classified as non_text without LLM call."""
        classifier = FakeIntentClassifier(intent="inquiry", confidence=0.9)
        node = build_route_intent_node(classifier, escalation_confidence_threshold=0.6)

        state = {"messages": [], "inbound_message_type": "image"}
        result = await node(state)

        assert result["intent"] == "non_text"
        assert result["intent_confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_messages_defaults_to_inquiry(self):
        """Empty messages list should default to inquiry with low confidence."""
        classifier = FakeIntentClassifier(intent="inquiry", confidence=0.9)
        node = build_route_intent_node(classifier, escalation_confidence_threshold=0.6)

        state = {"messages": []}
        result = await node(state)

        assert result["intent"] == "inquiry"
        assert result["intent_confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_does_not_escalate(self):
        """Confidence exactly at threshold should NOT escalate."""
        classifier = FakeIntentClassifier(intent="search", confidence=0.6)
        node = build_route_intent_node(classifier, escalation_confidence_threshold=0.6)

        state = {"messages": [{"role": "user", "content": "test"}]}
        result = await node(state)

        assert "escalation_flag" not in result


class TestRouteIntentEdge:
    """Tests for the route_intent edge routing function."""

    def test_escalation_flag_routes_to_escalate(self):
        state = {"escalation_flag": True, "intent": "inquiry"}
        assert route_intent_edge(state) == "escalate"

    def test_inquiry_routes_to_retrieve_rag(self):
        state = {"intent": "inquiry"}
        assert route_intent_edge(state) == "retrieve_rag"

    def test_search_routes_to_search_catalog(self):
        state = {"intent": "search"}
        assert route_intent_edge(state) == "search_catalog"

    def test_cart_add_routes_to_manage_cart(self):
        state = {"intent": "cart_add"}
        assert route_intent_edge(state) == "manage_cart"

    def test_cart_update_routes_to_manage_cart(self):
        state = {"intent": "cart_update"}
        assert route_intent_edge(state) == "manage_cart"

    def test_cart_remove_routes_to_manage_cart(self):
        state = {"intent": "cart_remove"}
        assert route_intent_edge(state) == "manage_cart"

    def test_checkout_request_routes_to_request_confirmation(self):
        state = {"intent": "checkout_request"}
        assert route_intent_edge(state) == "request_confirmation"

    def test_customer_confirmed_routes_to_create_order(self):
        state = {"intent": "customer_confirmed"}
        assert route_intent_edge(state) == "create_order"

    def test_support_request_routes_to_escalate(self):
        state = {"intent": "support_request"}
        assert route_intent_edge(state) == "escalate"

    def test_complaint_routes_to_escalate(self):
        state = {"intent": "complaint"}
        assert route_intent_edge(state) == "escalate"

    def test_non_text_routes_to_send_reply(self):
        state = {"intent": "non_text"}
        assert route_intent_edge(state) == "send_reply"

    def test_payment_question_routes_to_retrieve_rag(self):
        state = {"intent": "payment_question"}
        assert route_intent_edge(state) == "retrieve_rag"


# =============================================================================
# Tests: retrieve_rag node
# =============================================================================


class TestRetrieveRagNode:
    """Tests for the retrieve_rag node."""

    @pytest.mark.asyncio
    async def test_successful_retrieval_resets_misses(self):
        """Successful retrieval should reset consecutive_rag_misses to 0."""
        retriever = FakeRAGRetriever(results=[{"content": "Product info"}])
        node = build_retrieve_rag_node(retriever)

        state = {
            "inbound_message_text": "tell me about product X",
            "consecutive_rag_misses": 1,
        }
        result = await node(state)

        assert result["consecutive_rag_misses"] == 0
        assert len(result["rag_snippets"]) == 1

    @pytest.mark.asyncio
    async def test_first_miss_asks_clarifying_question(self):
        """First RAG miss should ask a clarifying question."""
        retriever = FakeRAGRetriever(results=[])
        node = build_retrieve_rag_node(retriever)

        state = {"inbound_message_text": "xyz", "consecutive_rag_misses": 0}
        result = await node(state)

        assert result["consecutive_rag_misses"] == 1
        assert result["reply_text"] is not None
        assert "escalation_flag" not in result

    @pytest.mark.asyncio
    async def test_second_consecutive_miss_escalates(self):
        """2nd consecutive RAG miss should trigger escalation."""
        retriever = FakeRAGRetriever(results=[])
        node = build_retrieve_rag_node(retriever)

        state = {"inbound_message_text": "xyz", "consecutive_rag_misses": 1}
        result = await node(state)

        assert result["consecutive_rag_misses"] == 2
        assert result["escalation_flag"] is True
        assert "2 consecutive RAG misses" in result["escalation_reason"]

    @pytest.mark.asyncio
    async def test_retriever_exception_counts_as_miss(self):
        """Retriever exception should be treated as a miss."""
        retriever = FakeRAGRetriever(should_fail=True)
        node = build_retrieve_rag_node(retriever)

        state = {"inbound_message_text": "test", "consecutive_rag_misses": 0}
        result = await node(state)

        assert result["consecutive_rag_misses"] == 1
        assert result["rag_snippets"] == []


class TestRetrieveRagEdge:
    """Tests for the retrieve_rag edge routing function."""

    def test_escalation_routes_to_escalate(self):
        state = {"escalation_flag": True, "rag_snippets": []}
        assert retrieve_rag_edge(state) == "escalate"

    def test_no_snippets_routes_to_send_reply(self):
        state = {"rag_snippets": []}
        assert retrieve_rag_edge(state) == "send_reply"

    def test_snippets_with_search_intent_routes_to_search_catalog(self):
        state = {"rag_snippets": [{"content": "info"}], "intent": "search"}
        assert retrieve_rag_edge(state) == "search_catalog"

    def test_snippets_with_inquiry_routes_to_recommend(self):
        state = {"rag_snippets": [{"content": "info"}], "intent": "inquiry"}
        assert retrieve_rag_edge(state) == "recommend"


# =============================================================================
# Tests: search_catalog node
# =============================================================================


class TestSearchCatalogNode:
    """Tests for the search_catalog node."""

    @pytest.mark.asyncio
    async def test_successful_search_resets_failures(self):
        """Successful search should reset consecutive_catalog_failures."""
        service = FakeCatalogSearchService(
            results=[{"product_id": "p1", "name": "Moisturizer"}]
        )
        node = build_search_catalog_node(service)

        state = {
            "inbound_message_text": "moisturizer",
            "consecutive_catalog_failures": 1,
        }
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 0
        assert len(result["last_search_results"]) == 1

    @pytest.mark.asyncio
    async def test_empty_results_sets_reply(self):
        """Zero results should set a reply message."""
        service = FakeCatalogSearchService(results=[])
        node = build_search_catalog_node(service)

        state = {"inbound_message_text": "nonexistent", "consecutive_catalog_failures": 0}
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 0
        assert result["reply_text"] is not None
        assert "tidak ada produk" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_first_failure_increments_counter(self):
        """First catalog failure should increment counter."""
        service = FakeCatalogSearchService(should_fail=True)
        node = build_search_catalog_node(service)

        state = {"inbound_message_text": "test", "consecutive_catalog_failures": 0}
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 1
        assert "escalation_flag" not in result

    @pytest.mark.asyncio
    async def test_second_consecutive_failure_escalates(self):
        """2nd consecutive catalog failure should trigger escalation."""
        service = FakeCatalogSearchService(should_fail=True)
        node = build_search_catalog_node(service)

        state = {"inbound_message_text": "test", "consecutive_catalog_failures": 1}
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 2
        assert result["escalation_flag"] is True
        assert "2 consecutive catalog tool failures" in result["escalation_reason"]


class TestSearchCatalogEdge:
    """Tests for the search_catalog edge routing function."""

    def test_escalation_routes_to_escalate(self):
        state = {"escalation_flag": True, "last_search_results": []}
        assert search_catalog_edge(state) == "escalate"

    def test_no_results_routes_to_send_reply(self):
        state = {"last_search_results": []}
        assert search_catalog_edge(state) == "send_reply"

    def test_results_found_routes_to_recommend(self):
        state = {"last_search_results": [{"product_id": "p1"}]}
        assert search_catalog_edge(state) == "recommend"


# =============================================================================
# Tests: recommend node
# =============================================================================


class TestRecommendNode:
    """Tests for the recommend node."""

    @pytest.mark.asyncio
    async def test_successful_recommendation(self):
        """Successful recommendation should set reply_text."""
        recommender = FakeRecommender(reply="Saya rekomendasikan produk A dan B!")
        node = build_recommend_node(recommender)

        state = {
            "messages": [{"role": "user", "content": "cari moisturizer"}],
            "last_search_results": [{"name": "Product A"}],
            "rag_snippets": [],
        }
        result = await node(state)

        assert result["reply_text"] == "Saya rekomendasikan produk A dan B!"

    @pytest.mark.asyncio
    async def test_recommender_failure_returns_fallback(self):
        """Recommender failure should return a fallback message."""
        recommender = FakeRecommender(should_fail=True)
        node = build_recommend_node(recommender)

        state = {"messages": [], "last_search_results": [], "rag_snippets": []}
        result = await node(state)

        assert "kendala" in result["reply_text"]


# =============================================================================
# Tests: manage_cart node
# =============================================================================


class TestManageCartNode:
    """Tests for the manage_cart node."""

    @pytest.mark.asyncio
    async def test_cart_add_success(self):
        """Successful cart add should reset failures and set reply."""
        service = FakeCartService(result={"ok": True, "cart_id": "c1", "subtotal": "100000"})
        node = build_manage_cart_node(service)

        state = {
            "intent": "cart_add",
            "customer_id": "cust-1",
            "last_search_results": [{"product_id": "p1", "variant_id": "v1"}],
            "consecutive_catalog_failures": 1,
        }
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 0
        assert "berhasil" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_cart_add_no_search_results(self):
        """Cart add without search results should ask to select product first."""
        service = FakeCartService()
        node = build_manage_cart_node(service)

        state = {
            "intent": "cart_add",
            "customer_id": "cust-1",
            "last_search_results": [],
            "consecutive_catalog_failures": 0,
        }
        result = await node(state)

        assert "pilih produk" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_cart_service_first_failure(self):
        """First cart service failure should increment counter."""
        service = FakeCartService(should_fail=True)
        node = build_manage_cart_node(service)

        state = {
            "intent": "cart_add",
            "customer_id": "cust-1",
            "last_search_results": [{"product_id": "p1", "variant_id": "v1"}],
            "consecutive_catalog_failures": 0,
        }
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 1
        assert "escalation_flag" not in result

    @pytest.mark.asyncio
    async def test_cart_service_second_failure_escalates(self):
        """2nd consecutive cart failure should escalate."""
        service = FakeCartService(should_fail=True)
        node = build_manage_cart_node(service)

        state = {
            "intent": "cart_add",
            "customer_id": "cust-1",
            "last_search_results": [{"product_id": "p1", "variant_id": "v1"}],
            "consecutive_catalog_failures": 1,
        }
        result = await node(state)

        assert result["consecutive_catalog_failures"] == 2
        assert result["escalation_flag"] is True


class TestManageCartEdge:
    """Tests for the manage_cart edge routing function."""

    def test_escalation_routes_to_escalate(self):
        state = {"escalation_flag": True}
        assert manage_cart_edge(state) == "escalate"

    def test_success_routes_to_send_reply(self):
        state = {}
        assert manage_cart_edge(state) == "send_reply"


# =============================================================================
# Tests: request_confirmation node
# =============================================================================


class TestRequestConfirmationNode:
    """Tests for the request_confirmation node."""

    @pytest.mark.asyncio
    async def test_generates_token_and_summary(self):
        """Should compute token and present cart summary."""
        snapshot = {
            "cart_id": "cart-1",
            "currency": "IDR",
            "subtotal": "200000",
            "items": [
                {
                    "cart_item_id": "ci-1",
                    "product_name": "Moisturizer",
                    "quantity": 2,
                    "unit_price_snapshot": "100000",
                },
            ],
        }
        service = FakeCartSnapshotService(snapshot=snapshot)
        node = build_request_confirmation_node(service)

        state = {"customer_id": "cust-1"}
        result = await node(state)

        assert result["pending_confirmation_token"] is not None
        assert len(result["pending_confirmation_token"]) == 64  # SHA-256 hex
        assert "Moisturizer" in result["reply_text"]
        assert result["active_cart_id"] == "cart-1"

    @pytest.mark.asyncio
    async def test_empty_cart_returns_message(self):
        """Empty cart should inform the customer."""
        service = FakeCartSnapshotService(snapshot={"cart_id": "c1", "items": []})
        node = build_request_confirmation_node(service)

        state = {"customer_id": "cust-1"}
        result = await node(state)

        assert "kosong" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_no_cart_returns_message(self):
        """No cart should inform the customer."""
        service = FakeCartSnapshotService(snapshot=None)
        node = build_request_confirmation_node(service)

        state = {"customer_id": "cust-1"}
        result = await node(state)

        assert "kosong" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_service_failure_returns_error_message(self):
        """Service failure should return an error message."""
        service = FakeCartSnapshotService(should_fail=True)
        node = build_request_confirmation_node(service)

        state = {"customer_id": "cust-1"}
        result = await node(state)

        assert "kendala" in result["reply_text"].lower()


class TestComputeConfirmationToken:
    """Tests for the confirmation token computation."""

    def test_deterministic_for_same_input(self):
        """Same input should always produce the same token."""
        snapshot = {
            "cart_id": "cart-1",
            "currency": "IDR",
            "items": [
                {"cart_item_id": "ci-1", "quantity": 2, "unit_price_snapshot": "100000"},
            ],
        }
        token1 = compute_confirmation_token(snapshot)
        token2 = compute_confirmation_token(snapshot)
        assert token1 == token2

    def test_different_input_produces_different_token(self):
        """Different inputs should produce different tokens."""
        snapshot1 = {
            "cart_id": "cart-1",
            "currency": "IDR",
            "items": [
                {"cart_item_id": "ci-1", "quantity": 2, "unit_price_snapshot": "100000"},
            ],
        }
        snapshot2 = {
            "cart_id": "cart-1",
            "currency": "IDR",
            "items": [
                {"cart_item_id": "ci-1", "quantity": 3, "unit_price_snapshot": "100000"},
            ],
        }
        assert compute_confirmation_token(snapshot1) != compute_confirmation_token(snapshot2)

    def test_item_order_does_not_affect_token(self):
        """Items in different order should produce the same token (sorted by cart_item_id)."""
        snapshot1 = {
            "cart_id": "cart-1",
            "currency": "IDR",
            "items": [
                {"cart_item_id": "ci-1", "quantity": 1, "unit_price_snapshot": "50000"},
                {"cart_item_id": "ci-2", "quantity": 2, "unit_price_snapshot": "75000"},
            ],
        }
        snapshot2 = {
            "cart_id": "cart-1",
            "currency": "IDR",
            "items": [
                {"cart_item_id": "ci-2", "quantity": 2, "unit_price_snapshot": "75000"},
                {"cart_item_id": "ci-1", "quantity": 1, "unit_price_snapshot": "50000"},
            ],
        }
        assert compute_confirmation_token(snapshot1) == compute_confirmation_token(snapshot2)


# =============================================================================
# Tests: create_order node
# =============================================================================


class TestCreateOrderNode:
    """Tests for the create_order node."""

    @pytest.mark.asyncio
    async def test_successful_order_creation(self):
        """Successful order should set last_order_id and clear token."""
        service = FakeOrderService(
            result={"ok": True, "order_id": "ord-1", "total": "200000", "currency": "IDR"}
        )
        node = build_create_order_node(service)

        state = {
            "customer_id": "cust-1",
            "active_cart_id": "cart-1",
            "pending_confirmation_token": "abc123",
        }
        result = await node(state)

        assert result["last_order_id"] == "ord-1"
        assert result["pending_confirmation_token"] is None

    @pytest.mark.asyncio
    async def test_token_mismatch_returns_error(self):
        """Token mismatch should inform customer and clear token."""
        service = FakeOrderService(
            result={"ok": False, "error": {"code": "token_mismatch", "message": "Token mismatch"}}
        )
        node = build_create_order_node(service)

        state = {
            "customer_id": "cust-1",
            "active_cart_id": "cart-1",
            "pending_confirmation_token": "wrong-token",
        }
        result = await node(state)

        assert "berubah" in result["reply_text"].lower()
        assert result["pending_confirmation_token"] is None
        assert "escalation_flag" not in result

    @pytest.mark.asyncio
    async def test_price_changed_returns_error(self):
        """Price changed should inform customer."""
        service = FakeOrderService(
            result={"ok": False, "error": {"code": "price_changed", "message": "Price changed"}}
        )
        node = build_create_order_node(service)

        state = {
            "customer_id": "cust-1",
            "active_cart_id": "cart-1",
            "pending_confirmation_token": "token",
        }
        result = await node(state)

        assert "harga" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_insufficient_stock_returns_error(self):
        """Insufficient stock should inform customer."""
        service = FakeOrderService(
            result={
                "ok": False,
                "error": {"code": "insufficient_stock", "message": "Out of stock"},
            }
        )
        node = build_create_order_node(service)

        state = {
            "customer_id": "cust-1",
            "active_cart_id": "cart-1",
            "pending_confirmation_token": "token",
        }
        result = await node(state)

        assert "stok" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_unrecoverable_error_escalates(self):
        """Unrecoverable error (exception) should escalate."""
        service = FakeOrderService(should_fail=True)
        node = build_create_order_node(service)

        state = {
            "customer_id": "cust-1",
            "active_cart_id": "cart-1",
            "pending_confirmation_token": "token",
        }
        result = await node(state)

        assert result["escalation_flag"] is True

    @pytest.mark.asyncio
    async def test_missing_cart_or_token_returns_error(self):
        """Missing cart_id or token should return error message."""
        service = FakeOrderService()
        node = build_create_order_node(service)

        state = {"customer_id": "cust-1", "active_cart_id": "", "pending_confirmation_token": ""}
        result = await node(state)

        assert "tidak ada keranjang" in result["reply_text"].lower()


class TestCreateOrderEdge:
    """Tests for the create_order edge routing function."""

    def test_escalation_routes_to_escalate(self):
        state = {"escalation_flag": True}
        assert create_order_edge(state) == "escalate"

    def test_order_created_routes_to_create_payment_link(self):
        state = {"last_order_id": "ord-1"}
        assert create_order_edge(state) == "create_payment_link"

    def test_validation_error_routes_to_send_reply(self):
        state = {"reply_text": "Error message", "last_order_id": ""}
        assert create_order_edge(state) == "send_reply"


# =============================================================================
# Tests: create_payment_link node
# =============================================================================


class TestCreatePaymentLinkNode:
    """Tests for the create_payment_link node."""

    @pytest.mark.asyncio
    async def test_successful_payment_link(self):
        """Successful payment link should set last_payment_link and reply."""
        service = FakePaymentService(
            result={"ok": True, "link_url": "https://pay.example.com/xyz"}
        )
        node = build_create_payment_link_node(service)

        state = {"last_order_id": "ord-1"}
        result = await node(state)

        assert result["last_payment_link"] == "https://pay.example.com/xyz"
        assert "https://pay.example.com/xyz" in result["reply_text"]

    @pytest.mark.asyncio
    async def test_provider_unavailable_escalates(self):
        """Provider unavailable should escalate."""
        service = FakePaymentService(should_fail=True)
        node = build_create_payment_link_node(service)

        state = {"last_order_id": "ord-1"}
        result = await node(state)

        assert result["escalation_flag"] is True
        assert "unavailable" in result["escalation_reason"].lower()

    @pytest.mark.asyncio
    async def test_provider_error_response_escalates(self):
        """Provider error response should escalate."""
        service = FakePaymentService(
            result={"ok": False, "error": {"code": "provider_unavailable"}}
        )
        node = build_create_payment_link_node(service)

        state = {"last_order_id": "ord-1"}
        result = await node(state)

        assert result["escalation_flag"] is True

    @pytest.mark.asyncio
    async def test_no_order_id_escalates(self):
        """Missing order_id should escalate."""
        service = FakePaymentService()
        node = build_create_payment_link_node(service)

        state = {"last_order_id": ""}
        result = await node(state)

        assert result["escalation_flag"] is True


class TestCreatePaymentLinkEdge:
    """Tests for the create_payment_link edge routing function."""

    def test_escalation_routes_to_escalate(self):
        state = {"escalation_flag": True}
        assert create_payment_link_edge(state) == "escalate"

    def test_success_routes_to_send_reply(self):
        state = {}
        assert create_payment_link_edge(state) == "send_reply"


# =============================================================================
# Tests: escalate node
# =============================================================================


class TestEscalateNode:
    """Tests for the escalate node."""

    @pytest.mark.asyncio
    async def test_sets_escalation_flag_and_reply(self):
        """Escalate should set flag, reason, and customer reply."""
        service = FakeEscalationService()
        node = build_escalate_node(service)

        state = {
            "conversation_id": "conv-1",
            "escalation_reason": "Customer requested support",
        }
        result = await node(state)

        assert result["escalation_flag"] is True
        assert result["escalation_reason"] == "Customer requested support"
        assert "tim kami" in result["reply_text"].lower()
        assert len(service.escalated) == 1

    @pytest.mark.asyncio
    async def test_escalation_service_failure_still_sets_flag(self):
        """Even if escalation service fails, flag should still be set."""
        service = FakeEscalationService(should_fail=True)
        node = build_escalate_node(service)

        state = {"conversation_id": "conv-1", "escalation_reason": "test"}
        result = await node(state)

        assert result["escalation_flag"] is True
        assert result["reply_text"] is not None

    @pytest.mark.asyncio
    async def test_no_service_still_works(self):
        """Node should work without an escalation service."""
        node = build_escalate_node(None)

        state = {"conversation_id": "conv-1", "escalation_reason": "test reason"}
        result = await node(state)

        assert result["escalation_flag"] is True
        assert result["escalation_reason"] == "test reason"


# =============================================================================
# Tests: send_reply node
# =============================================================================


class TestSendReplyNode:
    """Tests for the send_reply node."""

    @pytest.mark.asyncio
    async def test_enqueues_reply(self):
        """Should enqueue a WhatsAppSendTask and set reply_enqueued."""
        queue = FakeSendQueue()
        node = build_send_reply_node(queue)

        state = {
            "reply_text": "Hello customer!",
            "phone_e164": "+6281234567890",
            "conversation_id": "conv-1",
            "inbound_message_type": "text",
        }
        result = await node(state)

        assert result["reply_enqueued"] is True
        assert len(queue.items) == 1
        assert queue.items[0].body == "Hello customer!"
        assert queue.items[0].phone_e164 == "+6281234567890"

    @pytest.mark.asyncio
    async def test_no_reply_text_does_not_enqueue(self):
        """No reply_text should not enqueue anything."""
        queue = FakeSendQueue()
        node = build_send_reply_node(queue)

        state = {
            "reply_text": None,
            "phone_e164": "+6281234567890",
            "conversation_id": "conv-1",
            "inbound_message_type": "text",
        }
        result = await node(state)

        assert result["reply_enqueued"] is False
        assert len(queue.items) == 0

    @pytest.mark.asyncio
    async def test_non_text_message_gets_standard_reply(self):
        """Non-text inbound should get a standard 'text only' reply."""
        queue = FakeSendQueue()
        node = build_send_reply_node(queue)

        state = {
            "reply_text": "Some reply",
            "phone_e164": "+6281234567890",
            "conversation_id": "conv-1",
            "inbound_message_type": "image",
        }
        result = await node(state)

        assert result["reply_enqueued"] is True
        assert "teks" in result["reply_text"].lower()

    @pytest.mark.asyncio
    async def test_never_blocks_on_outbound_io(self):
        """send_reply should only enqueue, never make network calls."""
        queue = FakeSendQueue()
        node = build_send_reply_node(queue)

        state = {
            "reply_text": "Test",
            "phone_e164": "+6281234567890",
            "conversation_id": "conv-1",
            "inbound_message_type": "text",
        }
        # This should complete instantly (no network IO)
        result = await node(state)

        assert result["reply_enqueued"] is True
        # The queue item is a task object, not a network response
        task = queue.items[0]
        assert hasattr(task, "phone_e164")
        assert hasattr(task, "body")
        assert hasattr(task, "conversation_id")

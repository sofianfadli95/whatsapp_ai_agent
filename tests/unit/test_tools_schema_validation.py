"""Unit tests for tool input schema validation.

Verification: schema-validation unit tests reject malformed inputs without
contacting any service (Property 9).

These tests validate that Pydantic input schemas reject invalid inputs
before any service call is made.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.tools.catalog import (
    AddToCartInput,
    GetCartInput,
    RemoveFromCartInput,
    SearchProductsInput,
    UpdateCartItemQuantityInput,
)
from app.tools.order import CreateOrderInput
from app.tools.payment import CreatePaymentLinkInput
from app.tools.support import EscalateToHumanInput


# ---------------------------------------------------------------------------
# SearchProductsInput validation
# ---------------------------------------------------------------------------


class TestSearchProductsInputValidation:
    """Tests that SearchProductsInput rejects malformed inputs."""

    def test_valid_minimal_input(self) -> None:
        inp = SearchProductsInput(query="moisturizer")
        assert inp.query == "moisturizer"
        assert inp.price_min is None
        assert inp.price_max is None
        assert inp.category is None

    def test_valid_full_input(self) -> None:
        inp = SearchProductsInput(
            query="shoes", price_min=10.0, price_max=100.0, category="footwear"
        )
        assert inp.query == "shoes"
        assert inp.price_min == 10.0
        assert inp.price_max == 100.0
        assert inp.category == "footwear"

    def test_rejects_empty_query(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SearchProductsInput(query="")
        assert "query" in str(exc_info.value)

    def test_rejects_query_too_long(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SearchProductsInput(query="x" * 201)
        assert "query" in str(exc_info.value)

    def test_rejects_negative_price_min(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SearchProductsInput(query="test", price_min=-1.0)
        assert "price_min" in str(exc_info.value)

    def test_rejects_negative_price_max(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SearchProductsInput(query="test", price_max=-5.0)
        assert "price_max" in str(exc_info.value)

    def test_rejects_price_max_less_than_price_min(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SearchProductsInput(query="test", price_min=100.0, price_max=50.0)
        assert "price_max" in str(exc_info.value)

    def test_rejects_missing_query(self) -> None:
        with pytest.raises(ValidationError):
            SearchProductsInput()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# AddToCartInput validation
# ---------------------------------------------------------------------------


class TestAddToCartInputValidation:
    """Tests that AddToCartInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = AddToCartInput(
            customer_id="cust-1",
            product_id="prod-1",
            variant_id="var-1",
            quantity=3,
        )
        assert inp.quantity == 3

    def test_rejects_empty_customer_id(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="",
                product_id="prod-1",
                variant_id="var-1",
                quantity=1,
            )
        assert "customer_id" in str(exc_info.value)

    def test_rejects_customer_id_too_long(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="x" * 65,
                product_id="prod-1",
                variant_id="var-1",
                quantity=1,
            )
        assert "customer_id" in str(exc_info.value)

    def test_rejects_quantity_zero(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="cust-1",
                product_id="prod-1",
                variant_id="var-1",
                quantity=0,
            )
        assert "quantity" in str(exc_info.value)

    def test_rejects_quantity_negative(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="cust-1",
                product_id="prod-1",
                variant_id="var-1",
                quantity=-1,
            )
        assert "quantity" in str(exc_info.value)

    def test_rejects_quantity_over_999(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="cust-1",
                product_id="prod-1",
                variant_id="var-1",
                quantity=1000,
            )
        assert "quantity" in str(exc_info.value)

    def test_rejects_empty_product_id(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="cust-1",
                product_id="",
                variant_id="var-1",
                quantity=1,
            )
        assert "product_id" in str(exc_info.value)

    def test_rejects_empty_variant_id(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AddToCartInput(
                customer_id="cust-1",
                product_id="prod-1",
                variant_id="",
                quantity=1,
            )
        assert "variant_id" in str(exc_info.value)


# ---------------------------------------------------------------------------
# UpdateCartItemQuantityInput validation
# ---------------------------------------------------------------------------


class TestUpdateCartItemQuantityInputValidation:
    """Tests that UpdateCartItemQuantityInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = UpdateCartItemQuantityInput(
            customer_id="cust-1", cart_item_id="item-1", quantity=5
        )
        assert inp.quantity == 5

    def test_rejects_empty_customer_id(self) -> None:
        with pytest.raises(ValidationError):
            UpdateCartItemQuantityInput(
                customer_id="", cart_item_id="item-1", quantity=1
            )

    def test_rejects_empty_cart_item_id(self) -> None:
        with pytest.raises(ValidationError):
            UpdateCartItemQuantityInput(
                customer_id="cust-1", cart_item_id="", quantity=1
            )

    def test_rejects_quantity_zero(self) -> None:
        with pytest.raises(ValidationError):
            UpdateCartItemQuantityInput(
                customer_id="cust-1", cart_item_id="item-1", quantity=0
            )

    def test_rejects_quantity_over_999(self) -> None:
        with pytest.raises(ValidationError):
            UpdateCartItemQuantityInput(
                customer_id="cust-1", cart_item_id="item-1", quantity=1000
            )


# ---------------------------------------------------------------------------
# RemoveFromCartInput validation
# ---------------------------------------------------------------------------


class TestRemoveFromCartInputValidation:
    """Tests that RemoveFromCartInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = RemoveFromCartInput(customer_id="cust-1", cart_item_id="item-1")
        assert inp.customer_id == "cust-1"

    def test_rejects_empty_customer_id(self) -> None:
        with pytest.raises(ValidationError):
            RemoveFromCartInput(customer_id="", cart_item_id="item-1")

    def test_rejects_empty_cart_item_id(self) -> None:
        with pytest.raises(ValidationError):
            RemoveFromCartInput(customer_id="cust-1", cart_item_id="")

    def test_rejects_customer_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            RemoveFromCartInput(customer_id="x" * 65, cart_item_id="item-1")


# ---------------------------------------------------------------------------
# GetCartInput validation
# ---------------------------------------------------------------------------


class TestGetCartInputValidation:
    """Tests that GetCartInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = GetCartInput(customer_id="cust-1")
        assert inp.customer_id == "cust-1"

    def test_rejects_empty_customer_id(self) -> None:
        with pytest.raises(ValidationError):
            GetCartInput(customer_id="")

    def test_rejects_customer_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            GetCartInput(customer_id="x" * 65)


# ---------------------------------------------------------------------------
# CreateOrderInput validation
# ---------------------------------------------------------------------------


class TestCreateOrderInputValidation:
    """Tests that CreateOrderInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = CreateOrderInput(
            customer_id="cust-1",
            cart_id="cart-1",
            customer_confirmation_token="token-abc",
        )
        assert inp.customer_id == "cust-1"

    def test_rejects_empty_customer_id(self) -> None:
        with pytest.raises(ValidationError):
            CreateOrderInput(
                customer_id="",
                cart_id="cart-1",
                customer_confirmation_token="token-abc",
            )

    def test_rejects_empty_cart_id(self) -> None:
        with pytest.raises(ValidationError):
            CreateOrderInput(
                customer_id="cust-1",
                cart_id="",
                customer_confirmation_token="token-abc",
            )

    def test_rejects_empty_token(self) -> None:
        with pytest.raises(ValidationError):
            CreateOrderInput(
                customer_id="cust-1",
                cart_id="cart-1",
                customer_confirmation_token="",
            )

    def test_rejects_token_too_long(self) -> None:
        with pytest.raises(ValidationError):
            CreateOrderInput(
                customer_id="cust-1",
                cart_id="cart-1",
                customer_confirmation_token="x" * 129,
            )

    def test_rejects_customer_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            CreateOrderInput(
                customer_id="x" * 65,
                cart_id="cart-1",
                customer_confirmation_token="token-abc",
            )

    def test_rejects_cart_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            CreateOrderInput(
                customer_id="cust-1",
                cart_id="x" * 65,
                customer_confirmation_token="token-abc",
            )


# ---------------------------------------------------------------------------
# CreatePaymentLinkInput validation
# ---------------------------------------------------------------------------


class TestCreatePaymentLinkInputValidation:
    """Tests that CreatePaymentLinkInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = CreatePaymentLinkInput(order_id="order-1")
        assert inp.order_id == "order-1"

    def test_rejects_empty_order_id(self) -> None:
        with pytest.raises(ValidationError):
            CreatePaymentLinkInput(order_id="")

    def test_rejects_order_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            CreatePaymentLinkInput(order_id="x" * 65)


# ---------------------------------------------------------------------------
# EscalateToHumanInput validation
# ---------------------------------------------------------------------------


class TestEscalateToHumanInputValidation:
    """Tests that EscalateToHumanInput rejects malformed inputs."""

    def test_valid_input(self) -> None:
        inp = EscalateToHumanInput(
            conversation_id="conv-1", reason="Customer requested human"
        )
        assert inp.conversation_id == "conv-1"

    def test_rejects_empty_conversation_id(self) -> None:
        with pytest.raises(ValidationError):
            EscalateToHumanInput(conversation_id="", reason="some reason")

    def test_rejects_empty_reason(self) -> None:
        with pytest.raises(ValidationError):
            EscalateToHumanInput(conversation_id="conv-1", reason="")

    def test_rejects_reason_too_long(self) -> None:
        with pytest.raises(ValidationError):
            EscalateToHumanInput(conversation_id="conv-1", reason="x" * 501)

    def test_rejects_conversation_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            EscalateToHumanInput(conversation_id="x" * 65, reason="some reason")

"""Tools layer — LangChain-compatible tool wrappers for backend services.

Provides:
- ToolError: structured error model with machine-readable code.
- ToolResult[T]: generic envelope for tool responses (ok/error).
- AuditLogger protocol for dependency injection in tests.
"""

from __future__ import annotations

from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ToolError(BaseModel):
    """Structured error returned by a tool invocation."""

    code: str = Field(
        ...,
        description="Machine-readable error code (e.g. 'insufficient_stock', 'provider_unavailable').",
    )
    message: str = Field(
        ...,
        description="Human-readable error description.",
    )


class ToolResult(BaseModel, Generic[T]):
    """Uniform envelope for all tool responses.

    On success: ok=True, data=T, error=None.
    On failure: ok=False, data=None, error=ToolError.
    """

    ok: bool
    data: Any | None = None
    error: ToolError | None = None

    @classmethod
    def success(cls, data: Any) -> "ToolResult[T]":
        """Create a successful result."""
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: str, message: str) -> "ToolResult[T]":
        """Create a failure result."""
        return cls(ok=False, error=ToolError(code=code, message=message))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for LangChain tool return."""
        if self.ok:
            return {"ok": True, "data": self.data}
        return {"ok": False, "error": self.error.model_dump() if self.error else None}


class AuditLoggerProtocol(Protocol):
    """Protocol for the AuditLogger dependency used by tools."""

    async def emit(
        self,
        *,
        actor: str,
        event_type: str,
        tool_name: str | None = None,
        conversation_id: Any | None = None,
        customer_id: Any | None = None,
        order_id: Any | None = None,
        request_id: str | None = None,
        dedupe_key: str | None = None,
        input_redacted: dict[str, Any] | None = None,
        output_redacted: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None: ...


class CatalogServiceProtocol(Protocol):
    """Protocol for CatalogService dependency."""

    async def search(
        self,
        query: str,
        *,
        price_min: Any | None = None,
        price_max: Any | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> Any: ...

    async def add_to_cart(
        self,
        customer_id: Any,
        product_id: Any,
        variant_id: Any,
        quantity: int,
    ) -> Any: ...

    async def update_cart_item_quantity(
        self,
        customer_id: Any,
        cart_item_id: Any,
        quantity: int,
    ) -> Any: ...

    async def remove_from_cart(
        self,
        customer_id: Any,
        cart_item_id: Any,
    ) -> Any: ...

    async def get_cart(self, customer_id: Any) -> Any: ...


class OrderServiceProtocol(Protocol):
    """Protocol for OrderService dependency."""

    async def create_order(
        self,
        customer_id: Any,
        cart_id: Any,
        customer_confirmation_token: str,
    ) -> Any: ...


class PaymentServiceProtocol(Protocol):
    """Protocol for PaymentService dependency."""

    async def create_payment_link(self, order_id: Any) -> Any: ...


class HumanSupportServiceProtocol(Protocol):
    """Protocol for HumanSupportService dependency."""

    async def escalate(
        self,
        conversation_id: Any,
        *,
        reason: str,
        actor: str = "agent",
    ) -> None: ...

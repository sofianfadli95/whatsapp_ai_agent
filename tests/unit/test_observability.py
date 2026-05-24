"""Unit tests for the observability module.

Verification criteria (task 2.5):
- Log lines include `request_id` when bound via contextvars.
- Log lines include `conversation_id` when bound via contextvars.
- Credential-like substrings are NEVER present in log output (redaction works).
- The redaction processor handles various sensitive key patterns.
"""

from __future__ import annotations

import json
from io import StringIO

import structlog

from app.observability.logging import configure_logging, generate_request_id


def _capture_log_output(bind_vars: dict | None = None, log_kwargs: dict | None = None) -> dict:
    """Helper: configure logging, bind context vars, emit a log, return parsed JSON."""
    output = StringIO()

    # Configure structlog with a custom logger factory that writes to our StringIO
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # Import the redaction processor from our module
            __import__("app.observability.logging", fromlist=["_redact_processor"])._redact_processor,
            __import__("app.observability.logging", fromlist=["_request_id_binder"])._request_id_binder,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=False,
    )

    # Clear any previous context
    structlog.contextvars.clear_contextvars()

    # Bind context variables if provided
    if bind_vars:
        structlog.contextvars.bind_contextvars(**bind_vars)

    logger = structlog.stdlib.get_logger("test")

    log_kwargs = log_kwargs or {}
    logger.info("test_event", **log_kwargs)

    structlog.contextvars.clear_contextvars()

    raw = output.getvalue().strip()
    if not raw:
        return {}
    return json.loads(raw)


class TestRequestIdBinding:
    """Test that request_id appears in log output when bound via contextvars."""

    def test_request_id_present_in_log_when_bound(self) -> None:
        request_id = "req-12345-abcde"
        result = _capture_log_output(bind_vars={"request_id": request_id})
        assert result.get("request_id") == request_id

    def test_request_id_absent_when_not_bound(self) -> None:
        result = _capture_log_output()
        assert "request_id" not in result

    def test_generate_request_id_returns_uuid4_format(self) -> None:
        rid = generate_request_id()
        # UUID4 format: 8-4-4-4-12 hex chars
        parts = rid.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12


class TestConversationIdBinding:
    """Test that conversation_id appears in log output when bound via contextvars."""

    def test_conversation_id_present_in_log_when_bound(self) -> None:
        conv_id = "conv-98765-fghij"
        result = _capture_log_output(bind_vars={"conversation_id": conv_id})
        assert result.get("conversation_id") == conv_id

    def test_both_request_id_and_conversation_id_present(self) -> None:
        request_id = "req-aaa-bbb"
        conv_id = "conv-ccc-ddd"
        result = _capture_log_output(
            bind_vars={"request_id": request_id, "conversation_id": conv_id}
        )
        assert result.get("request_id") == request_id
        assert result.get("conversation_id") == conv_id


class TestRedactionProcessor:
    """Test that credential-like substrings are never present in log output."""

    def test_secret_key_is_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"my_secret": "super-secret-value-123"}
        )
        assert result.get("my_secret") == "***REDACTED***"
        assert "super-secret-value-123" not in json.dumps(result)

    def test_password_key_is_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"db_password": "p@ssw0rd!"}
        )
        assert result.get("db_password") == "***REDACTED***"
        assert "p@ssw0rd!" not in json.dumps(result)

    def test_token_key_is_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"access_token": "eyJhbGciOiJIUzI1NiJ9.payload.sig"}
        )
        assert result.get("access_token") == "***REDACTED***"
        assert "eyJhbGciOiJIUzI1NiJ9" not in json.dumps(result)

    def test_api_key_is_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"openai_api_key": "sk-abc123def456"}
        )
        assert result.get("openai_api_key") == "***REDACTED***"
        assert "sk-abc123def456" not in json.dumps(result)

    def test_authorization_key_is_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"authorization": "Bearer my-secret-token"}
        )
        assert result.get("authorization") == "***REDACTED***"
        assert "Bearer my-secret-token" not in json.dumps(result)

    def test_credential_key_is_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"user_credential": "cred-xyz-789"}
        )
        assert result.get("user_credential") == "***REDACTED***"
        assert "cred-xyz-789" not in json.dumps(result)

    def test_non_sensitive_keys_are_not_redacted(self) -> None:
        result = _capture_log_output(
            log_kwargs={"user_name": "alice", "order_id": "ord-123"}
        )
        assert result.get("user_name") == "alice"
        assert result.get("order_id") == "ord-123"

    def test_mixed_sensitive_and_non_sensitive(self) -> None:
        result = _capture_log_output(
            log_kwargs={
                "user_name": "bob",
                "api_key": "sk-secret-key-value",
                "order_id": "ord-456",
                "webhook_secret": "whsec_abc123",
            }
        )
        assert result.get("user_name") == "bob"
        assert result.get("order_id") == "ord-456"
        assert result.get("api_key") == "***REDACTED***"
        assert result.get("webhook_secret") == "***REDACTED***"
        # Ensure no credential values leak anywhere in the serialized output
        serialized = json.dumps(result)
        assert "sk-secret-key-value" not in serialized
        assert "whsec_abc123" not in serialized

    def test_key_pattern_matching_is_case_insensitive(self) -> None:
        result = _capture_log_output(
            log_kwargs={
                "API_KEY": "key-upper",
                "Secret_Value": "secret-mixed",
                "PASSWORD": "pass-upper",
            }
        )
        assert result.get("API_KEY") == "***REDACTED***"
        assert result.get("Secret_Value") == "***REDACTED***"
        assert result.get("PASSWORD") == "***REDACTED***"

    def test_redaction_with_request_id_context(self) -> None:
        """Ensure redaction works alongside request_id binding."""
        result = _capture_log_output(
            bind_vars={"request_id": "req-test-123"},
            log_kwargs={"auth_token": "bearer-xyz"},
        )
        assert result.get("request_id") == "req-test-123"
        assert result.get("auth_token") == "***REDACTED***"
        assert "bearer-xyz" not in json.dumps(result)

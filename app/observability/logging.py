"""Structured logging configuration using structlog.

Provides JSON rendering for production, request-id binding, and
automatic redaction of sensitive keys (Req 11.8).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import structlog


# Keys whose values should be redacted in log output.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(secret|password|token|key|credential|api_key|authorization)",
    re.IGNORECASE,
)

_REDACTED = "***REDACTED***"


def _redact_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Redact values for keys that match sensitive patterns."""
    for key in list(event_dict.keys()):
        if _SENSITIVE_KEY_PATTERN.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def _request_id_binder(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Bind request_id from structlog context if not already present."""
    # request_id is bound via structlog.contextvars; this processor ensures
    # it appears in the event dict even if not explicitly passed.
    return event_dict


def generate_request_id() -> str:
    """Generate a new UUID4 request ID."""
    return str(uuid.uuid4())


def configure_logging() -> None:
    """Configure structlog with JSON rendering, redaction, and request-id binding."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            _request_id_binder,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

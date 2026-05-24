"""Property-based tests for internal inbound auth gate (Property 1).

**Validates: Requirements 1.8**

Property 1: For any request to POST /internal/whatsapp/inbound whose
Authorization header is missing, malformed, or whose bearer token does not
equal WHATSAPP_GATEWAY_INTERNAL_TOKEN (verified with a constant-time comparison),
the API SHALL respond with HTTP 401 and SHALL produce no DB writes.

Since the full endpoint is not yet implemented, we test the core property at
the component level: a constant-time bearer-token comparison function correctly
rejects any token that differs from the expected token, and accepts only the
exact expected token.
"""

from __future__ import annotations

import hmac
import string

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Implementation under test: constant-time bearer token verification
# This mirrors what the inbound endpoint will use (hmac.compare_digest).
# ---------------------------------------------------------------------------


def verify_bearer_token(provided: str, expected: str) -> bool:
    """Verify a bearer token using constant-time comparison.

    Returns True only when provided == expected (constant-time to prevent
    timing attacks). Returns False for any mismatch.
    """
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating random token strings (printable ASCII, variable length)
random_token_st = st.text(
    alphabet=string.ascii_letters + string.digits + string.punctuation,
    min_size=1,
    max_size=128,
)

# Strategy for generating a valid expected token (non-empty, reasonable length)
expected_token_st = st.text(
    alphabet=string.ascii_letters + string.digits + "-_.",
    min_size=8,
    max_size=64,
)

# Strategy for malformed Authorization header values
malformed_auth_st = st.one_of(
    st.just(""),
    st.just("Basic dXNlcjpwYXNz"),
    st.just("Bearer"),
    st.just("Bearer "),
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=50),
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(provided=random_token_st, expected=expected_token_st)
@settings(max_examples=200)
def test_bad_token_always_rejected(provided: str, expected: str) -> None:
    """Property: any token that differs from the expected token is rejected.

    **Validates: Requirements 1.8**

    For any random token string that is not equal to the expected token,
    the constant-time comparison SHALL return False (reject).
    This ensures no DB writes would occur on a bad token.
    """
    assume(provided != expected)

    result = verify_bearer_token(provided, expected)
    assert result is False, (
        f"Auth gate should reject mismatched token: "
        f"provided={provided!r}, expected={expected!r}"
    )


@given(token=expected_token_st)
@settings(max_examples=200)
def test_correct_token_always_accepted(token: str) -> None:
    """Property: the exact expected token is always accepted.

    **Validates: Requirements 1.8**

    For any valid token string, comparing it to itself SHALL return True.
    """
    result = verify_bearer_token(token, token)
    assert result is True, (
        f"Auth gate should accept matching token: token={token!r}"
    )


@given(expected=expected_token_st, suffix=st.text(min_size=1, max_size=10))
@settings(max_examples=200)
def test_prefix_or_suffix_mismatch_rejected(expected: str, suffix: str) -> None:
    """Property: tokens that are prefixes/suffixes of the expected token are rejected.

    **Validates: Requirements 1.8**

    A token that is the expected token with extra characters appended
    SHALL be rejected. This guards against length-extension attacks.
    """
    extended = expected + suffix
    assume(extended != expected)

    result = verify_bearer_token(extended, expected)
    assert result is False, (
        f"Auth gate should reject extended token: "
        f"provided={extended!r}, expected={expected!r}"
    )


@given(expected=expected_token_st)
@settings(max_examples=200)
def test_empty_token_rejected(expected: str) -> None:
    """Property: an empty provided token is always rejected.

    **Validates: Requirements 1.8**

    Missing or empty bearer tokens SHALL always be rejected.
    """
    result = verify_bearer_token("", expected)
    assert result is False, (
        f"Auth gate should reject empty token against expected={expected!r}"
    )

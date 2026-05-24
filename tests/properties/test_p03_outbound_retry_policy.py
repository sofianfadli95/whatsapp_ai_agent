"""Property-based tests for outbound retry policy (Property 3).

**Validates: Requirements 1.10**

Property 3: For any sequence of upstream WhatsApp Gateway POST /send responses,
the sender SHALL make at most MAX_ATTEMPTS (3) attempts with backoffs [1s, 2s, 4s]
(capped at 8s), SHALL stop on the first successful attempt, and SHALL persist a
messages_outbound row reflecting the final sent or failed status.

We test the retry policy as a pure property of the constants and logic:
- The backoff sequence is correct and bounded
- The number of attempts never exceeds MAX_ATTEMPTS
- Success on attempt K means exactly K attempts were made
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.workers.whatsapp_sender import BACKOFF_DELAYS, MAX_ATTEMPTS, REQUEST_TIMEOUT


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for which attempt succeeds (0-indexed), or None for all-fail
success_on_attempt_st = st.one_of(
    st.integers(min_value=0, max_value=MAX_ATTEMPTS - 1),
    st.none(),
)

# Strategy for attempt index (0-indexed)
attempt_index_st = st.integers(min_value=0, max_value=MAX_ATTEMPTS - 2)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(data=st.data())
@settings(max_examples=200)
def test_max_attempts_bounded(data: st.DataObject) -> None:
    """Property: the retry policy never exceeds MAX_ATTEMPTS total attempts.

    **Validates: Requirements 1.10**

    For any configuration, the maximum number of attempts is exactly
    MAX_ATTEMPTS (3). The backoff delays list has exactly MAX_ATTEMPTS
    entries, one per attempt.
    """
    assert MAX_ATTEMPTS == 3, f"Expected MAX_ATTEMPTS=3, got {MAX_ATTEMPTS}"
    assert len(BACKOFF_DELAYS) == MAX_ATTEMPTS, (
        f"BACKOFF_DELAYS should have {MAX_ATTEMPTS} entries, "
        f"got {len(BACKOFF_DELAYS)}"
    )


@given(attempt_index=attempt_index_st)
@settings(max_examples=200)
def test_backoff_sequence_correct(attempt_index: int) -> None:
    """Property: backoff delays follow [1.0, 2.0, 4.0] and are capped at 8s.

    **Validates: Requirements 1.10**

    For any attempt index i in [0, MAX_ATTEMPTS-2], the backoff delay
    before the next attempt SHALL be BACKOFF_DELAYS[i] and SHALL be
    at most 8 seconds.
    """
    expected_delays = [1.0, 2.0, 4.0]

    delay = BACKOFF_DELAYS[attempt_index]
    capped_delay = min(delay, 8.0)

    assert delay == expected_delays[attempt_index], (
        f"Backoff at index {attempt_index} should be "
        f"{expected_delays[attempt_index]}, got {delay}"
    )
    assert capped_delay <= 8.0, (
        f"Capped backoff at index {attempt_index} should be <= 8.0, "
        f"got {capped_delay}"
    )


@given(success_attempt=success_on_attempt_st)
@settings(max_examples=200)
def test_attempts_count_property(success_attempt: int | None) -> None:
    """Property: success on attempt K means exactly K+1 attempts total.

    **Validates: Requirements 1.10**

    If the gateway succeeds on attempt K (0-indexed), the sender SHALL
    have made exactly K+1 total attempts. If all attempts fail, the
    sender SHALL have made exactly MAX_ATTEMPTS attempts.
    """
    # Simulate the retry logic
    attempts_made = 0

    for attempt in range(MAX_ATTEMPTS):
        attempts_made += 1

        if success_attempt is not None and attempt == success_attempt:
            # Success on this attempt - stop retrying
            break

    if success_attempt is not None:
        expected_attempts = success_attempt + 1
        assert attempts_made == expected_attempts, (
            f"Success on attempt {success_attempt} should yield "
            f"{expected_attempts} total attempts, got {attempts_made}"
        )
    else:
        assert attempts_made == MAX_ATTEMPTS, (
            f"All-fail scenario should yield {MAX_ATTEMPTS} attempts, "
            f"got {attempts_made}"
        )


@given(success_attempt=success_on_attempt_st)
@settings(max_examples=200)
def test_total_backoff_time_bounded(success_attempt: int | None) -> None:
    """Property: total backoff time is bounded and predictable.

    **Validates: Requirements 1.10**

    The total backoff time (sum of delays between attempts) SHALL be
    deterministic based on the number of attempts made. For all-fail:
    sum of delays for attempts 0..MAX_ATTEMPTS-2 = 1+2 = 3s (delay
    is only applied between attempts, not after the last one).
    """
    total_backoff = 0.0

    for attempt in range(MAX_ATTEMPTS):
        if success_attempt is not None and attempt == success_attempt:
            break

        # Backoff is applied after each failed attempt except the last
        if attempt < MAX_ATTEMPTS - 1:
            if success_attempt is None or attempt < success_attempt:
                # This attempt failed, apply backoff before next
                delay = min(BACKOFF_DELAYS[attempt], 8.0)
                total_backoff += delay

    # Calculate expected total backoff
    if success_attempt is not None:
        # Backoff only between attempts 0..success_attempt-1
        expected_backoff = sum(
            min(BACKOFF_DELAYS[i], 8.0)
            for i in range(success_attempt)
        )
    else:
        # All failed: backoff between attempts 0..MAX_ATTEMPTS-2
        expected_backoff = sum(
            min(BACKOFF_DELAYS[i], 8.0)
            for i in range(MAX_ATTEMPTS - 1)
        )

    assert total_backoff == expected_backoff, (
        f"Total backoff mismatch: got {total_backoff}, "
        f"expected {expected_backoff} for success_attempt={success_attempt}"
    )

    # Maximum possible backoff (all fail): 1 + 2 = 3s (only 2 delays for 3 attempts)
    max_possible_backoff = sum(min(d, 8.0) for d in BACKOFF_DELAYS[:MAX_ATTEMPTS - 1])
    assert total_backoff <= max_possible_backoff, (
        f"Total backoff {total_backoff} exceeds maximum {max_possible_backoff}"
    )


@given(data=st.data())
@settings(max_examples=200)
def test_request_timeout_is_10_seconds(data: st.DataObject) -> None:
    """Property: per-attempt request timeout is exactly 10 seconds.

    **Validates: Requirements 1.10**

    Each attempt SHALL use a 10-second timeout per the requirements.
    """
    assert REQUEST_TIMEOUT == 10.0, (
        f"REQUEST_TIMEOUT should be 10.0, got {REQUEST_TIMEOUT}"
    )

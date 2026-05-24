"""Property-based tests for phone normalization (Property 4).

**Validates: Requirements 2.1, 2.2**

Property 4: Phone normalization is idempotent — normalizing an already-normalized
E.164 number returns the same value. Invalid numbers return None.
"""

from __future__ import annotations

import string

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.utils.phone import normalize_to_e164


# --- Strategies ---

# Strategy for generating plausible valid phone numbers (Indonesian format)
# Indonesian mobile numbers: +62 followed by 8xx or 9xx, total 10-12 digits after country code
indonesian_mobile_st = st.from_regex(
    r"\+628[0-9]{8,11}", fullmatch=True
)

# Strategy for numbers that are already in E.164 format (various countries)
e164_numbers_st = st.one_of(
    # Indonesian mobile: +628xxxxxxxxxx (10-12 digits after +62)
    st.from_regex(r"\+628[0-9]{8,11}", fullmatch=True),
    # US numbers: +1xxxxxxxxxx
    st.from_regex(r"\+1[2-9][0-9]{9}", fullmatch=True),
    # UK mobile: +447xxxxxxxxx
    st.from_regex(r"\+447[0-9]{9}", fullmatch=True),
)

# Strategy for clearly invalid phone strings (no extractable valid digit sequences)
invalid_phone_st = st.one_of(
    # Pure alphabetic strings (no digits at all)
    st.text(
        alphabet=string.ascii_letters,
        min_size=1,
        max_size=30,
    ),
    # Too short digit strings (fewer than 7 digits, cannot form valid number)
    st.from_regex(r"[0-9]{1,4}", fullmatch=True),
    # Empty-ish strings
    st.just(""),
    st.just("   "),
    st.just("+"),
    st.just("++123"),
    # Invalid E.164 prefix with too few digits
    st.from_regex(r"\+[0-9]{1,3}", fullmatch=True),
    # Numbers with invalid country code (+0 is not valid)
    st.from_regex(r"\+0[0-9]{5,10}", fullmatch=True),
)


# --- Property Tests ---


@given(phone=e164_numbers_st)
@settings(max_examples=200)
def test_idempotence_property(phone: str) -> None:
    """Property: normalizing an already-normalized E.164 number returns the same value.

    **Validates: Requirements 2.1**

    For any valid phone number that normalizes successfully,
    applying normalize_to_e164 again yields the same result.
    """
    first_pass = normalize_to_e164(phone)
    assume(first_pass is not None)  # Only test idempotence on valid numbers

    second_pass = normalize_to_e164(first_pass)
    assert second_pass == first_pass, (
        f"Idempotence violated: normalize({phone!r}) = {first_pass!r}, "
        f"but normalize({first_pass!r}) = {second_pass!r}"
    )


@given(text=invalid_phone_st)
@settings(max_examples=200)
def test_rejection_on_invalid(text: str) -> None:
    """Property: random strings that aren't valid phone numbers return None.

    **Validates: Requirements 2.2**

    Invalid inputs (non-numeric, too short, gibberish) must be rejected.
    """
    result = normalize_to_e164(text)
    assert result is None, (
        f"Expected None for invalid input {text!r}, got {result!r}"
    )


@given(phone=e164_numbers_st)
@settings(max_examples=200)
def test_output_format_property(phone: str) -> None:
    """Property: valid results always start with '+' and contain only digits after '+'.

    **Validates: Requirements 2.1**

    The E.164 format requires a leading '+' followed by digits only.
    """
    result = normalize_to_e164(phone)
    assume(result is not None)

    assert result.startswith("+"), f"E.164 must start with '+', got {result!r}"
    digits_part = result[1:]
    assert digits_part.isdigit(), (
        f"E.164 must have only digits after '+', got {result!r}"
    )


@given(phone=e164_numbers_st)
@settings(max_examples=200)
def test_e164_length_property(phone: str) -> None:
    """Property: valid E.164 results have 8 to 15 digits (per Req 2.1).

    **Validates: Requirements 2.1**

    E.164 numbers must have between 8 and 15 digits (excluding the '+' prefix).
    """
    result = normalize_to_e164(phone)
    assume(result is not None)

    digits_part = result[1:]
    digit_count = len(digits_part)
    assert 8 <= digit_count <= 15, (
        f"E.164 digit count must be 8-15, got {digit_count} for {result!r}"
    )

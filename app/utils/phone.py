"""Phone number normalization utility.

Normalizes raw phone strings to E.164 format using the phonenumbers library.
Returns None for invalid inputs. Idempotent for valid E.164 numbers.
"""

from __future__ import annotations

import phonenumbers


def normalize_to_e164(raw: str) -> str | None:
    """Normalize a raw phone string to E.164 format.

    Args:
        raw: A raw phone number string (e.g., "081234567890", "+6281234567890").

    Returns:
        The E.164 formatted string (e.g., "+6281234567890") if valid,
        or None if the input cannot be parsed or is not a valid phone number.

    The function is idempotent: calling it on an already-normalized E.164 string
    returns the same string unchanged.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None

    raw = raw.strip()

    try:
        # If the number starts with '+', parse without a default region
        # Otherwise, assume Indonesia (ID) as default region
        if raw.startswith("+"):
            parsed = phonenumbers.parse(raw, None)
        else:
            parsed = phonenumbers.parse(raw, "ID")
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    # Validate E.164 length constraint: 8 to 15 digits total (per Req 2.1)
    digits = formatted[1:]  # strip leading '+'
    if not (8 <= len(digits) <= 15):
        return None

    return formatted

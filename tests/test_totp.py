"""Tests for the stdlib-only RFC 6238 TOTP helper."""

from __future__ import annotations

import pytest

from custom_components.omv import totp

# RFC 6238 Appendix B test secret: ASCII "12345678901234567890" in Base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # RFC 6238 Appendix B SHA-1 vectors, truncated to 6 digits.
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ],
)
def test_generate_code_rfc6238_vectors(timestamp: int, expected: str) -> None:
    """Codes match the RFC 6238 SHA-1 reference vectors."""
    assert totp.generate_code(RFC_SECRET, timestamp) == expected


def test_generate_code_uses_current_time_by_default() -> None:
    """Without a timestamp the code matches the current 30s window."""
    import time

    now = time.time()
    assert totp.generate_code(RFC_SECRET) in {
        totp.generate_code(RFC_SECRET, now),
        # Guard against the window flipping between the two calls.
        totp.generate_code(RFC_SECRET, now + totp.TIME_STEP),
    }


@pytest.mark.parametrize(
    "messy",
    [
        "gezdgnbvgy3tqojqgezdgnbvgy3tqojq",  # lower-case
        "GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ",  # grouped with spaces
        "GEZD-GNBV-GY3T-QOJQ-GEZD-GNBV-GY3T-QOJQ",  # grouped with dashes
    ],
)
def test_generate_code_tolerates_secret_formatting(messy: str) -> None:
    """Common authenticator-app secret formats are normalized before decoding."""
    assert totp.generate_code(messy, 59) == "287082"


def test_generate_code_pads_missing_base32_padding() -> None:
    """Secrets without '=' padding are padded to a multiple of 8 chars."""
    unpadded = "GEZDGNBVGY3TQOJQGEZDGNBVGY"  # 26 chars -> needs padding
    assert totp.generate_code(unpadded, 59) == totp.generate_code(unpadded + "======", 59)


@pytest.mark.parametrize(
    ("secret", "valid"),
    [
        (RFC_SECRET, True),
        ("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", True),
        ("", False),
        ("   ", False),
        ("not-base32!!", False),
        ("1nvalid8", False),  # '1' is not in the Base32 alphabet
    ],
)
def test_is_valid_secret(secret: str, valid: bool) -> None:
    """is_valid_secret accepts decodable secrets and rejects garbage."""
    assert totp.is_valid_secret(secret) is valid


def test_generate_code_invalid_secret_raises_value_error() -> None:
    """Invalid Base32 input raises ValueError instead of a cryptic binascii error."""
    with pytest.raises(ValueError):
        totp.generate_code("not-base32!!", 59)
    with pytest.raises(ValueError):
        totp.generate_code("", 59)

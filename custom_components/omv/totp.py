"""RFC 6238 TOTP code generator using only the standard library.

Implemented with stdlib ``base64``/``hmac`` instead of a PyPI dependency so
the integration's ``manifest.json`` ``requirements`` stays empty
(HACS-friendly, no version pinning against Home Assistant core) — the same
rationale as :mod:`custom_components.omv.wol`.

Only the parameters used by OMV's ``openmediavault-2fa-totp`` plugin are
supported: HMAC-SHA1, 30-second time step, 6 digits.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time

#: TOTP time step in seconds (RFC 6238 default, used by OMV's TOTP plugin).
TIME_STEP = 30
_DIGITS = 6


def _normalize_secret(secret: str) -> str:
    """Return the Base32 secret in canonical form (upper-case, padded).

    Tolerates the formats authenticator apps commonly present: lower-case
    letters, whitespace/dash grouping (``abcd efgh``) and missing ``=``
    padding.

    Args:
        secret: The raw Base32-encoded shared secret as entered by the user.

    Returns:
        The normalized Base32 string, padded to a multiple of 8 characters.
    """
    compact = "".join(secret.split()).replace("-", "").upper()
    if remainder := len(compact) % 8:
        compact += "=" * (8 - remainder)
    return compact


def _decode_secret(secret: str) -> bytes:
    """Decode a Base32 TOTP secret into its raw key bytes.

    Args:
        secret: The Base32-encoded shared secret (normalization tolerant).

    Returns:
        The decoded key bytes.

    Raises:
        ValueError: When the secret is empty or not valid Base32.
    """
    normalized = _normalize_secret(secret)
    if not normalized:
        raise ValueError("TOTP secret is empty")
    try:
        return base64.b32decode(normalized)
    except (binascii.Error, ValueError) as err:
        raise ValueError(f"Invalid Base32 TOTP secret: {err}") from err


def is_valid_secret(secret: str) -> bool:
    """Return whether a string is a usable Base32 TOTP secret.

    Args:
        secret: The candidate shared secret.

    Returns:
        True when the secret decodes to a non-empty key, False otherwise.
    """
    try:
        return bool(_decode_secret(secret))
    except ValueError:
        return False


def generate_code(secret: str, timestamp: float | None = None) -> str:
    """Generate the 6-digit TOTP code for a shared secret (RFC 6238, SHA-1).

    Args:
        secret: The Base32-encoded shared secret.
        timestamp: Unix timestamp to generate the code for. Defaults to the
            current time; pass ``time.time() - TIME_STEP`` /
            ``time.time() + TIME_STEP`` for the neighbouring windows to
            tolerate clock skew.

    Returns:
        The zero-padded 6-digit code valid for the 30-second window
        containing ``timestamp``.

    Raises:
        ValueError: When the secret is empty or not valid Base32.
    """
    key = _decode_secret(secret)
    if timestamp is None:
        timestamp = time.time()
    counter = int(timestamp // TIME_STEP)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    (code,) = struct.unpack(">I", digest[offset : offset + 4])
    code &= 0x7FFFFFFF
    return str(code % 10**_DIGITS).zfill(_DIGITS)

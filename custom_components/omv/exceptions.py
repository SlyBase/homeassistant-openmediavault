"""Exceptions for the OpenMediaVault integration."""


class OMVApiError(Exception):
    """Base exception for OMV API errors."""


class OMVAuthError(OMVApiError):
    """Authentication failed or OMV session expired."""


class OMVTwoFactorRequiredError(OMVAuthError):
    """OMV requires a second authentication step (e.g. a TOTP code)."""

    def __init__(self, message: str, *, challenge_kind: str | None = None) -> None:
        """Initialize the exception.

        Args:
            message: Human-readable description of the challenge.
            challenge_kind: The OMV challenge kind (e.g. ``"totp"``), if known.
        """
        super().__init__(message)
        self.challenge_kind = challenge_kind


class OMVConnectionError(OMVApiError):
    """Cannot connect to the OMV endpoint."""

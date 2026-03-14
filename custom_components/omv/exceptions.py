"""Exceptions for the OpenMediaVault integration."""


class OMVApiError(Exception):
    """Base exception for OMV API errors."""


class OMVAuthError(OMVApiError):
    """Authentication failed or OMV session expired."""


class OMVConnectionError(OMVApiError):
    """Cannot connect to the OMV endpoint."""
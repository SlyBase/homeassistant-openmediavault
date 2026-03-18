"""Diagnostics support for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

TO_REDACT = {
    "username",
    "password",
    # Hardware identifiers that can de-anonymise a user's device
    "serialnumber",
    "address",   # IP address of network interfaces
    "netmask",
    "gateway",
    "macaddress",
    "mac",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = config_entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(config_entry.data, TO_REDACT),
            "options": async_redact_data(config_entry.options, TO_REDACT),
        },
        "data": async_redact_data(coordinator.data, TO_REDACT),
    }

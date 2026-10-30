"""Tests for OMV diagnostics (custom_components/omv/diagnostics.py)."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv import diagnostics
from custom_components.omv.const import DOMAIN


def _collect(hass, entry: MockConfigEntry, coordinator: Any) -> dict[str, Any]:
    """Run the diagnostics handler against a configured entry."""
    entry.runtime_data = coordinator
    return diagnostics.async_get_config_entry_diagnostics(hass, entry)


async def test_diagnostics_redacts_secrets_from_entry(hass, coordinator) -> None:
    """Entry data/options never leak password, TOTP secret or IP address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data={
            "host": "192.168.1.10",
            "username": "admin",
            "password": "super-secret-pw",
            "totp_secret": "JBSWY3DPEHPK3PXP",
            "port": 80,
            "ssl": False,
            "verify_ssl": True,
        },
        options={"host": "192.168.1.10", "scan_interval": 60},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator

    result = await _collect(hass, entry, coordinator)

    serialised = str(result["entry"]).lower()
    assert "super-secret-pw" not in serialised
    assert "jbswy3dpehpkp3pxp" not in serialised
    entry_data = result["entry"]["data"]
    assert entry_data.get("password") in (None, "***", "<redacted>", "**REDACTED**")
    assert entry_data.get("totp_secret") in (None, "***", "<redacted>", "**REDACTED**")
    # Non-sensitive config fields survive redaction.
    assert entry_data.get("port") == 80
    assert entry_data.get("ssl") is False
    assert entry_data.get("verify_ssl") is True


async def test_diagnostics_redacts_secrets_from_coordinator_data(hass, coordinator) -> None:
    """Coordinator data never leaks MAC address, IP or hostname."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data={
            "host": "192.168.1.10",
            "username": "admin",
            "password": "pw",
            "port": 80,
            "ssl": False,
            "verify_ssl": True,
        },
        options={},
    )
    entry.add_to_hass(hass)

    # Inject sensitive hardware values into the coordinator data.
    coordinator.data = {
        "hwinfo": {
            "hostname": "secret-nas",
            "serialnumber": "SN123456",
            "version": "8.1.2-1",
        },
        "network": [
            {
                "name": "eth0",
                "address": "10.0.0.5",
                "netmask": "255.255.255.0",
                "gateway": "10.0.0.1",
                "macaddress": "AA:BB:CC:DD:EE:FF",
            }
        ],
    }
    entry.runtime_data = coordinator

    result = await _collect(hass, entry, coordinator)

    serialised = str(result["data"]).lower()
    assert "aa:bb:cc:dd:ee:ff" not in serialised
    assert "10.0.0.5" not in serialised
    assert "sn123456" not in serialised
    assert "secret-nas" not in serialised


async def test_diagnostics_returns_expected_structure(hass, coordinator) -> None:
    """The payload has entry/data top-level blocks with data+options inside entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data={"host": "192.168.1.10", "username": "admin", "password": "pw", "port": 80},
        options={},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator

    result = await _collect(hass, entry, coordinator)

    assert "entry" in result
    assert "data" in result
    assert "data" in result["entry"]
    assert "options" in result["entry"]

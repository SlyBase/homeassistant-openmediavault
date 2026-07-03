"""Tests for OMV integration setup (custom_components/omv/__init__.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv import session_handoff
from custom_components.omv.const import DOMAIN

ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_PORT: 80,
    CONF_SSL: False,
    CONF_VERIFY_SSL: True,
}


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=ENTRY_DATA,
    )


@pytest.mark.asyncio
async def test_async_setup_entry_reuses_handed_off_session(hass) -> None:
    """A session stashed by the config flow is reused instead of a fresh OMV login."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    handed_off_api = AsyncMock()
    system_info = {"hostname": "nas", "version": "8.1.2-1"}
    session_handoff.store("nas", handed_off_api, system_info)

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(side_effect=AssertionError("should not open a new OMV login")),
        ),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator.async_init",
            new=AsyncMock(),
        ) as mock_async_init,
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    mock_async_init.assert_awaited_once_with(system_info)
    assert entry.runtime_data.api is handed_off_api
    # The stashed session was consumed and is gone from the registry.
    assert session_handoff.pop("nas") is None


@pytest.mark.asyncio
async def test_async_setup_entry_connects_fresh_without_handoff(hass) -> None:
    """Without a stashed session, setup opens a normal OMV login as before."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    system_info = {"hostname": "nas", "version": "8.1.2-1"}

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(return_value=system_info),
        ) as mock_connect,
        patch("custom_components.omv.OMVAPI.async_close", new=AsyncMock()),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator.async_init",
            new=AsyncMock(),
        ) as mock_async_init,
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    mock_connect.assert_awaited_once()
    mock_async_init.assert_awaited_once_with(system_info)

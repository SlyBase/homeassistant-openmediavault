"""Tests for the OMV config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv.const import CONF_SCAN_INTERVAL, CONF_SMART_DISABLED, DOMAIN
from custom_components.omv.exceptions import OMVAuthError

USER_INPUT = {
    CONF_HOST: "192.168.1.10",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_PORT: 80,
    CONF_SSL: False,
    CONF_VERIFY_SSL: True,
}


def _field_marker(schema: vol.Schema, field_name: str) -> vol.Marker:
    """Return the schema marker for a field."""
    for marker in schema.schema:
        if marker.schema == field_name:
            return marker
    raise AssertionError(f"Field {field_name} not found in schema")


@pytest.mark.asyncio
async def test_flow_user_success(hass) -> None:
    """Test the happy path for the user flow."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        assert result["type"] == "form"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "OMV (nas)"


@pytest.mark.asyncio
async def test_flow_auth_error(hass) -> None:
    """Test the invalid_auth path."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVAuthError("invalid")),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"

    defaults = result["data_schema"]({})

    assert defaults[CONF_HOST] == USER_INPUT[CONF_HOST]
    assert defaults[CONF_USERNAME] == USER_INPUT[CONF_USERNAME]
    assert defaults[CONF_PORT] == USER_INPUT[CONF_PORT]
    assert defaults[CONF_SSL] is USER_INPUT[CONF_SSL]
    assert defaults[CONF_VERIFY_SSL] is USER_INPUT[CONF_VERIFY_SSL]
    assert defaults[CONF_PASSWORD] == ""


@pytest.mark.asyncio
async def test_flow_duplicate_abort(hass, config_entry) -> None:
    """Test duplicate hostnames are rejected via unique_id."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=config_entry.data,
        options=config_entry.options,
    )
    config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_options_flow(hass, config_entry) -> None:
    """Test the options flow persists values."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 120, CONF_SMART_DISABLED: True},
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_SCAN_INTERVAL: 120, CONF_SMART_DISABLED: True}

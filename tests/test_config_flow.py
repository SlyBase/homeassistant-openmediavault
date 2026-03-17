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
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv.const import (
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_ZFS_POOLS,
    CONF_SMART_DISABLED,
    CONF_VIRTUAL_PASSTHROUGH,
    DOMAIN,
)
from custom_components.omv.exceptions import OMVAuthError

USER_INPUT = {
    CONF_HOST: "192.0.2.10",
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


def _selector_values(field_selector: selector.SelectSelector) -> list[str]:
    """Extract selector option values for assertions."""
    config = field_selector.config
    options = config["options"] if isinstance(config, dict) else config.options
    return [str(option["value"]) for option in options]


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
async def test_options_flow_uses_live_inventory_and_defaults_to_all(hass, config_entry) -> None:
    """Test the options flow exposes unfiltered live inventory with defaults."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
            CONF_SELECTED_SERVICES: ["ssh"],
            CONF_SELECTED_NETWORK_INTERFACES: ["net-1"],
            CONF_SELECTED_RAIDS: ["md0"],
            CONF_SELECTED_ZFS_POOLS: ["tank"],
            CONF_SELECTED_COMPOSE_PROJECTS: ["paperless"],
            CONF_SELECTED_CONTAINERS: ["ctr-paperless-app"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [
                    {"value": "sda", "label": "sda"},
                    {"value": "sdb", "label": "sdb"},
                ],
                CONF_SELECTED_FILESYSTEMS: [
                    {"value": "fs-1", "label": "data"},
                    {"value": "fs-2", "label": "backup"},
                ],
                CONF_SELECTED_SERVICES: [{"value": "ssh", "label": "SSH"}],
                CONF_SELECTED_NETWORK_INTERFACES: [
                    {"value": "net-1", "label": "eth0"},
                    {"value": "net-2", "label": "eth1"},
                ],
                CONF_SELECTED_RAIDS: [{"value": "md0", "label": "md0"}],
                CONF_SELECTED_ZFS_POOLS: [{"value": "tank", "label": "tank"}],
                CONF_SELECTED_COMPOSE_PROJECTS: [
                    {"value": "paperless", "label": "paperless (2)"},
                ],
                CONF_SELECTED_CONTAINERS: [
                    {"value": "ctr-paperless-app", "label": "paperless-app [paperless]"},
                    {"value": "ctr-db", "label": "db [paperless]"},
                ],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] == "form"
    defaults = result["data_schema"]({})
    assert defaults[CONF_SELECTED_DISKS] == ["sda"]
    assert defaults[CONF_SELECTED_COMPOSE_PROJECTS] == ["paperless"]
    assert defaults[CONF_SELECTED_CONTAINERS] == ["ctr-paperless-app"]

    disk_marker = _field_marker(result["data_schema"], CONF_SELECTED_DISKS)
    disk_selector = result["data_schema"].schema[disk_marker]
    assert _selector_values(disk_selector) == ["sda", "sdb"]


@pytest.mark.asyncio
async def test_options_flow_defaults_to_all_when_selection_was_never_set(
    hass, config_entry
) -> None:
    """Test new entries default to all currently available resources."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [
                    {"value": "sda", "label": "sda"},
                    {"value": "sdb", "label": "sdb"},
                ],
                CONF_SELECTED_FILESYSTEMS: [{"value": "fs-1", "label": "data"}],
                CONF_SELECTED_SERVICES: [{"value": "ssh", "label": "SSH"}],
                CONF_SELECTED_NETWORK_INTERFACES: [{"value": "net-1", "label": "eth0"}],
                CONF_SELECTED_RAIDS: [{"value": "md0", "label": "md0"}],
                CONF_SELECTED_ZFS_POOLS: [{"value": "tank", "label": "tank"}],
                CONF_SELECTED_COMPOSE_PROJECTS: [
                    {"value": "paperless", "label": "paperless (2)"}
                ],
                CONF_SELECTED_CONTAINERS: [
                    {"value": "ctr-paperless-app", "label": "paperless-app [paperless]"}
                ],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_SELECTED_DISKS] == ["sda", "sdb"]
    assert defaults[CONF_SELECTED_FILESYSTEMS] == ["fs-1"]
    assert defaults[CONF_SELECTED_COMPOSE_PROJECTS] == ["paperless"]
    assert defaults[CONF_SELECTED_CONTAINERS] == ["ctr-paperless-app"]


@pytest.mark.asyncio
async def test_options_flow_does_not_auto_select_new_compose_resources_on_existing_entries(
    hass, config_entry
) -> None:
    """Test newly introduced compose fields stay unselected on existing entries."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [{"value": "sda", "label": "sda"}],
                CONF_SELECTED_FILESYSTEMS: [{"value": "fs-1", "label": "data"}],
                CONF_SELECTED_SERVICES: [],
                CONF_SELECTED_NETWORK_INTERFACES: [],
                CONF_SELECTED_RAIDS: [],
                CONF_SELECTED_ZFS_POOLS: [],
                CONF_SELECTED_COMPOSE_PROJECTS: [
                    {"value": "vaultwarden", "label": "vaultwarden (1)"}
                ],
                CONF_SELECTED_CONTAINERS: [
                    {"value": "ctr-vaultwarden", "label": "vaultwarden [vaultwarden]"}
                ],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_SELECTED_COMPOSE_PROJECTS] == []
    assert defaults[CONF_SELECTED_CONTAINERS] == []


@pytest.mark.asyncio
async def test_options_flow_persists_missing_resource_fields(hass, config_entry) -> None:
    """Test missing multiselect fields do not clear persisted options."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
            CONF_SELECTED_SERVICES: ["ssh"],
            CONF_SELECTED_NETWORK_INTERFACES: ["net-1"],
            CONF_SELECTED_RAIDS: ["md0"],
            CONF_SELECTED_ZFS_POOLS: ["tank"],
            CONF_SELECTED_COMPOSE_PROJECTS: ["paperless"],
            CONF_SELECTED_CONTAINERS: ["ctr-paperless-app"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                field: []
                for field in (
                    CONF_SELECTED_DISKS,
                    CONF_SELECTED_FILESYSTEMS,
                    CONF_SELECTED_SERVICES,
                    CONF_SELECTED_NETWORK_INTERFACES,
                    CONF_SELECTED_RAIDS,
                    CONF_SELECTED_ZFS_POOLS,
                    CONF_SELECTED_COMPOSE_PROJECTS,
                    CONF_SELECTED_CONTAINERS,
                )
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 120, CONF_SMART_DISABLED: True},
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SCAN_INTERVAL] == 120
    assert result["data"][CONF_SMART_DISABLED] is True
    assert result["data"][CONF_SELECTED_DISKS] == ["sda"]
    assert result["data"][CONF_SELECTED_FILESYSTEMS] == ["fs-1"]
    assert result["data"][CONF_SELECTED_COMPOSE_PROJECTS] == ["paperless"]
    assert result["data"][CONF_SELECTED_CONTAINERS] == ["ctr-paperless-app"]


@pytest.mark.asyncio
async def test_options_flow_virtual_passthrough_forces_smart_disabled(
    hass, config_entry
) -> None:
    """Test virtual passthrough always persists with SMART disabled."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                field: []
                for field in (
                    CONF_SELECTED_DISKS,
                    CONF_SELECTED_FILESYSTEMS,
                    CONF_SELECTED_SERVICES,
                    CONF_SELECTED_NETWORK_INTERFACES,
                    CONF_SELECTED_RAIDS,
                    CONF_SELECTED_ZFS_POOLS,
                    CONF_SELECTED_COMPOSE_PROJECTS,
                    CONF_SELECTED_CONTAINERS,
                )
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 120,
            CONF_SMART_DISABLED: False,
            CONF_VIRTUAL_PASSTHROUGH: True,
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_VIRTUAL_PASSTHROUGH] is True
    assert result["data"][CONF_SMART_DISABLED] is True

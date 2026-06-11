"""Tests for OMV container switches."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.omv.exceptions import OMVApiError
from custom_components.omv.switch import (
    OMVContainerSwitch,
    async_setup_entry,
    get_expected_switch_unique_ids,
)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_container_switches(coordinator, config_entry) -> None:
    """Test the switch platform creates one switch per compose container."""
    added: list[OMVContainerSwitch] = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    entry_id = coordinator.config_entry.entry_id
    unique_ids = {entity.unique_id for entity in added}
    assert unique_ids == {
        f"{entry_id}-container-ctr-paperless-app",
        f"{entry_id}-container-ctr-nginx",
        f"{entry_id}-container-ctr-vaultwarden",
        f"{entry_id}-container-ctr-db",
    }


def test_get_expected_switch_unique_ids(coordinator) -> None:
    """Test expected switch unique IDs match the current compose containers."""
    entry_id = coordinator.config_entry.entry_id

    assert get_expected_switch_unique_ids(coordinator) == {
        f"{entry_id}-container-ctr-paperless-app",
        f"{entry_id}-container-ctr-nginx",
        f"{entry_id}-container-ctr-vaultwarden",
        f"{entry_id}-container-ctr-db",
    }


@pytest.mark.asyncio
async def test_container_switch_initial_state_reflects_running(coordinator) -> None:
    """Test the switch reports on/off based on the container's running flag."""
    running_container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-vaultwarden")
    stopped_container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-db")

    running_switch = OMVContainerSwitch(coordinator, running_container)
    stopped_switch = OMVContainerSwitch(coordinator, stopped_container)

    assert running_switch.is_on is True
    assert stopped_switch.is_on is False
    assert running_switch.device_info["identifiers"] == {
        ("omv", f"{coordinator.config_entry.entry_id}:container:ctr-vaultwarden")
    }
    assert running_switch._attr_suggested_object_id == "nas_container_vaultwarden"


@pytest.mark.asyncio
async def test_container_switch_turn_on_calls_doContainerCommand(coordinator) -> None:
    """Test turning on a switch issues a Compose start command and refreshes."""
    coordinator.async_request_refresh = AsyncMock()
    container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-db")
    switch = OMVContainerSwitch(coordinator, container)
    switch.hass = coordinator.hass
    switch.entity_id = "switch.nas_container_db"

    await switch.async_turn_on()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doContainerCommand",
        {"id": "ctr-db", "command": "start", "command2": ""},
    )
    assert switch.is_on is True
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_container_switch_turn_off_calls_doContainerCommand(coordinator) -> None:
    """Test turning off a switch issues a Compose stop command and refreshes."""
    coordinator.async_request_refresh = AsyncMock()
    container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-vaultwarden")
    switch = OMVContainerSwitch(coordinator, container)
    switch.hass = coordinator.hass
    switch.entity_id = "switch.nas_container_vaultwarden"

    await switch.async_turn_off()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doContainerCommand",
        {"id": "ctr-vaultwarden", "command": "stop", "command2": ""},
    )
    assert switch.is_on is False
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_container_switch_raises_on_api_error(coordinator) -> None:
    """Test a failing RPC call raises a translated HomeAssistantError."""
    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-db")
    switch = OMVContainerSwitch(coordinator, container)
    switch.hass = coordinator.hass
    switch.entity_id = "switch.nas_container_db"

    with pytest.raises(HomeAssistantError) as exc_info:
        await switch.async_turn_on()

    assert exc_info.value.translation_key == "container_command_failed"
    assert exc_info.value.translation_placeholders == {"resource": "ctr-db", "command": "start"}
    coordinator.async_request_refresh.assert_awaited_once()

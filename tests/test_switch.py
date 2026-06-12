"""Tests for OMV container switches."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.omv.exceptions import OMVApiError
from custom_components.omv.switch import (
    OMVContainerSwitch,
    OMVVmSwitch,
    async_setup_entry,
    get_expected_switch_unique_ids,
)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_container_and_vm_switches(coordinator, config_entry) -> None:
    """Test the switch platform creates one switch per container and per VM."""
    added: list[OMVContainerSwitch | OMVVmSwitch] = []

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
        f"{entry_id}-vm-vm-uuid-1234",
    }


def test_get_expected_switch_unique_ids(coordinator) -> None:
    """Test expected switch unique IDs match the current containers and VMs."""
    entry_id = coordinator.config_entry.entry_id

    assert get_expected_switch_unique_ids(coordinator) == {
        f"{entry_id}-container-ctr-paperless-app",
        f"{entry_id}-container-ctr-nginx",
        f"{entry_id}-container-ctr-vaultwarden",
        f"{entry_id}-container-ctr-db",
        f"{entry_id}-vm-vm-uuid-1234",
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


@pytest.mark.asyncio
async def test_vm_switch_initial_state_and_device(coordinator) -> None:
    """Test the VM switch reflects the running flag and attaches to the VM device."""
    vm = next(v for v in coordinator.data["kvm"] if v["vm_key"] == "vm-uuid-1234")

    switch = OMVVmSwitch(coordinator, vm)

    assert switch.is_on is True
    assert switch.device_info["identifiers"] == {("omv", f"{coordinator.config_entry.entry_id}:vm:vm-uuid-1234")}
    assert switch._attr_suggested_object_id == "nas_vm_homeassistant"


@pytest.mark.asyncio
async def test_vm_switch_turn_on_calls_kvm_do_command(coordinator) -> None:
    """Test turning on a VM switch issues Kvm.doCommand poweron and refreshes."""
    coordinator.async_request_refresh = AsyncMock()
    vm = next(v for v in coordinator.data["kvm"] if v["vm_key"] == "vm-uuid-1234")
    switch = OMVVmSwitch(coordinator, vm)
    switch.hass = coordinator.hass
    switch.entity_id = "switch.nas_vm_homeassistant"

    await switch.async_turn_on()

    coordinator.api.async_call.assert_awaited_once_with(
        "Kvm",
        "doCommand",
        {
            "command": "poweron",
            "name": "homeassistant",
            "virttype": "vm",
            "vncport": "n/a",
            "spiceport": "n/a",
            "hostport": "n/a",
            "hostport2": "n/a",
        },
    )
    assert switch.is_on is True
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_vm_switch_turn_off_calls_kvm_do_command(coordinator) -> None:
    """Test turning off a VM switch issues a graceful Kvm.doCommand poweroff."""
    coordinator.async_request_refresh = AsyncMock()
    vm = next(v for v in coordinator.data["kvm"] if v["vm_key"] == "vm-uuid-1234")
    switch = OMVVmSwitch(coordinator, vm)
    switch.hass = coordinator.hass
    switch.entity_id = "switch.nas_vm_homeassistant"

    await switch.async_turn_off()

    coordinator.api.async_call.assert_awaited_once_with(
        "Kvm",
        "doCommand",
        {
            "command": "poweroff",
            "name": "homeassistant",
            "virttype": "vm",
            "vncport": "n/a",
            "spiceport": "n/a",
            "hostport": "n/a",
            "hostport2": "n/a",
        },
    )
    assert switch.is_on is False
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_vm_switch_raises_on_api_error(coordinator) -> None:
    """Test a failing Kvm.doCommand raises a translated HomeAssistantError."""
    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    vm = next(v for v in coordinator.data["kvm"] if v["vm_key"] == "vm-uuid-1234")
    switch = OMVVmSwitch(coordinator, vm)
    switch.hass = coordinator.hass
    switch.entity_id = "switch.nas_vm_homeassistant"

    with pytest.raises(HomeAssistantError) as exc_info:
        await switch.async_turn_off()

    assert exc_info.value.translation_key == "vm_command_failed"
    assert exc_info.value.translation_placeholders == {
        "resource": "homeassistant",
        "command": "poweroff",
    }
    coordinator.async_request_refresh.assert_awaited_once()

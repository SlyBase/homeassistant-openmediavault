"""Tests for OMV button entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.omv.button import (
    OMVComposeProjectButton,
    OMVComposeSystemButton,
    OMVRebootButton,
    OMVShutdownButton,
    async_setup_entry,
    get_expected_button_unique_ids,
)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_buttons(coordinator, config_entry) -> None:
    """Test button platform setup."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 19
    assert added[2].unique_id.endswith("01-compose_up-paperless")
    assert added[3].unique_id.endswith("02-compose_down-paperless")
    assert added[4].unique_id.endswith("03-compose_start-paperless")
    assert added[5].unique_id.endswith("04-compose_stop-paperless")
    assert added[6].unique_id.endswith("05-compose_pull-paperless")
    assert added[-2].unique_id.endswith("98-compose_image_prune")
    assert added[-1].unique_id.endswith("99-compose_container_prune")
    assert added[2]._attr_suggested_object_id == "nas_01_compose_paperless_up"


@pytest.mark.asyncio
async def test_async_setup_entry_omits_prune_buttons_without_docker_service(coordinator, config_entry) -> None:
    """Test global Docker prune buttons only appear when the service exists."""
    coordinator.data["service"] = [{"name": "ssh", "title": "SSH", "enabled": True, "running": True}]
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 17
    assert not any(entity.unique_id.endswith("98-compose_image_prune") for entity in added)
    assert not any(entity.unique_id.endswith("99-compose_container_prune") for entity in added)


@pytest.mark.asyncio
async def test_reboot_button_calls_reboot(coordinator) -> None:
    """Test reboot button RPC call."""
    coordinator.api.async_call = AsyncMock()
    button = OMVRebootButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "reboot")


@pytest.mark.asyncio
async def test_shutdown_button_calls_shutdown(coordinator) -> None:
    """Test shutdown button RPC call."""
    coordinator.api.async_call = AsyncMock()
    button = OMVShutdownButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "shutdown")


@pytest.mark.asyncio
async def test_compose_project_button_calls_do_command_and_refresh(coordinator) -> None:
    """Test compose project buttons trigger OMV compose commands."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVComposeProjectButton(
        coordinator,
        coordinator.data["compose_projects"][0],
        1,
        "compose_up",
        "up -d",
        "mdi:arrow-up-bold-box-outline",
    )

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doCommand",
        {"uuid": "proj-paperless", "command": "up -d"},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_system_button_calls_do_command_and_refresh(coordinator) -> None:
    """Test global compose maintenance buttons trigger OMV compose commands."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVComposeSystemButton(
        coordinator,
        98,
        "compose_image_prune",
        "image prune -f",
        "mdi:image-remove-outline",
    )

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doCommand",
        {"command": "image prune -f"},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_project_button_reads_background_output_when_present(coordinator) -> None:
    """Test compose project commands resolve OMV background output files."""

    async def async_call(service, method, params=None):
        if (service, method) == ("Compose", "doCommand"):
            return {"filename": "compose-up.log"}
        if (service, method) == ("Exec", "getOutput"):
            return {"running": False, "output": "started"}
        raise AssertionError((service, method, params))

    coordinator.api.async_call = AsyncMock(side_effect=async_call)
    coordinator.async_request_refresh = AsyncMock()
    button = OMVComposeProjectButton(
        coordinator,
        coordinator.data["compose_projects"][0],
        1,
        "compose_up",
        "up -d",
        "mdi:arrow-up-bold-box-outline",
    )

    await button.async_press()

    assert coordinator.api.async_call.await_args_list[0].args == (
        "Compose",
        "doCommand",
        {"uuid": "proj-paperless", "command": "up -d"},
    )
    assert coordinator.api.async_call.await_args_list[1].args == (
        "Exec",
        "getOutput",
        {"filename": "compose-up.log", "pos": 0},
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_get_expected_button_unique_ids_includes_compose_project_actions(
    coordinator,
    config_entry,
) -> None:
    """Test cleanup state includes dynamically created compose project buttons."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-reboot" in unique_ids
    assert f"{config_entry.entry_id}-03-compose_start-paperless" in unique_ids
    assert f"{config_entry.entry_id}-05-compose_pull-web" in unique_ids
    assert f"{config_entry.entry_id}-98-compose_image_prune" in unique_ids
    assert f"{config_entry.entry_id}-99-compose_container_prune" in unique_ids


def test_get_expected_button_unique_ids_omits_prune_buttons_without_docker_service(
    coordinator,
    config_entry,
) -> None:
    """Test cleanup state drops prune button IDs when Docker is absent."""
    coordinator.data["service"] = [{"name": "ssh", "title": "SSH"}]

    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-98-compose_image_prune" not in unique_ids
    assert f"{config_entry.entry_id}-99-compose_container_prune" not in unique_ids

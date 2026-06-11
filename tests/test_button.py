"""Tests for OMV button entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.omv.button import (
    OMVApplyConfigButton,
    OMVComposeProjectButton,
    OMVComposeSystemButton,
    OMVContainerRestartButton,
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

    assert len(added) == 24
    assert added[3].unique_id.endswith("01-compose_up-paperless")
    assert added[4].unique_id.endswith("02-compose_down-paperless")
    assert added[5].unique_id.endswith("03-compose_start-paperless")
    assert added[6].unique_id.endswith("04-compose_stop-paperless")
    assert added[7].unique_id.endswith("05-compose_pull-paperless")
    assert added[18].unique_id.endswith("98-compose_image_prune")
    assert added[19].unique_id.endswith("99-compose_container_prune")
    assert added[3]._attr_suggested_object_id == "nas_01_compose_paperless_up"
    assert added[-1].unique_id.endswith("container_restart-ctr-db")
    assert added[-1]._attr_suggested_object_id == "nas_container_db_restart"


@pytest.mark.asyncio
async def test_async_setup_entry_omits_prune_buttons_without_docker_service(coordinator, config_entry) -> None:
    """Test global Docker prune buttons only appear when the service exists."""
    coordinator.data["service"] = [{"name": "ssh", "title": "SSH", "enabled": True, "running": True}]
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 22
    assert not any(entity.unique_id.endswith("98-compose_image_prune") for entity in added)
    assert not any(entity.unique_id.endswith("99-compose_container_prune") for entity in added)


@pytest.mark.asyncio
async def test_reboot_button_calls_reboot(coordinator) -> None:
    """Test reboot button RPC call."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
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


@pytest.mark.asyncio
async def test_apply_config_button_calls_api(coordinator) -> None:
    """Test apply_config button calls async_apply_config on the API."""
    coordinator.api.async_apply_config = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVApplyConfigButton(coordinator)

    await button.async_press()

    coordinator.api.async_apply_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_config_button_triggers_refresh(coordinator) -> None:
    """Test apply_config button triggers coordinator refresh before and after the call."""
    coordinator.api.async_apply_config = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVApplyConfigButton(coordinator)

    await button.async_press()

    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_apply_config_button_raises_on_api_error(coordinator) -> None:
    """Test apply_config button raises HomeAssistantError when API call fails."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator.api.async_apply_config = AsyncMock(side_effect=Exception("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    button = OMVApplyConfigButton(coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "apply_config_failed"


@pytest.mark.asyncio
async def test_reboot_button_blocks_when_config_dirty(coordinator) -> None:
    """Test reboot button raises HomeAssistantError when configDirty is True."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator.data["hwinfo"]["configDirty"] = True
    coordinator.data["hwinfo"]["dirtyModules"] = ["nginx", "samba"]
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api.async_call = AsyncMock()
    button = OMVRebootButton(coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "reboot_blocked_config_dirty"
    coordinator.api.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_reboot_button_modules_in_placeholder(coordinator) -> None:
    """Test reboot button includes dirty module names in translation placeholders."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator.data["hwinfo"]["configDirty"] = True
    coordinator.data["hwinfo"]["dirtyModules"] = ["nginx", "samba"]
    coordinator.async_request_refresh = AsyncMock()
    button = OMVRebootButton(coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_placeholders == {"modules": "nginx, samba"}


@pytest.mark.asyncio
async def test_reboot_button_proceeds_when_clean(coordinator) -> None:
    """Test reboot button calls System.reboot when config is clean."""
    coordinator.data["hwinfo"]["configDirty"] = False
    coordinator.data["hwinfo"]["dirtyModules"] = []
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api.async_call = AsyncMock()
    button = OMVRebootButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "reboot")


@pytest.mark.asyncio
async def test_container_restart_button_calls_do_container_command_and_refresh(coordinator) -> None:
    """Test container restart button triggers Compose.doContainerCommand and a refresh."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-db")
    button = OMVContainerRestartButton(coordinator, container)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doContainerCommand",
        {"id": "ctr-db", "command": "restart", "command2": ""},
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_get_expected_button_unique_ids_includes_apply_config(
    coordinator,
    config_entry,
) -> None:
    """Test get_expected_button_unique_ids includes the apply_config button."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-apply_config" in unique_ids

"""Tests for OMV update entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.update import UpdateEntityFeature

from custom_components.omv.const import DOMAIN
from custom_components.omv.update import (
    OMVUpdateEntity,
    async_setup_entry,
    get_expected_update_unique_ids,
)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_one_update_entity(coordinator, config_entry) -> None:
    """Test that async_setup_entry registers exactly one update entity."""
    added: list = []

    def add_entities(entities: list) -> None:
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 1
    assert isinstance(added[0], OMVUpdateEntity)


def test_installed_version(coordinator) -> None:
    """Test installed_version returns the OMV version from hwinfo."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.installed_version == "8.1.2-1"


def test_latest_version_with_updates(coordinator) -> None:
    """Test latest_version returns a synthetic string when updates are pending."""
    # sample_data has availablePkgUpdates=3, pkgUpdatesAvailable=True
    entity = OMVUpdateEntity(coordinator)
    assert entity.latest_version == "8.1.2-1 (+3 packages)"
    assert entity.latest_version != entity.installed_version


def test_latest_version_no_updates(coordinator, sample_data) -> None:
    """Test latest_version equals installed_version when no updates are available."""
    sample_data["hwinfo"]["availablePkgUpdates"] = 0
    sample_data["hwinfo"]["pkgUpdatesAvailable"] = False
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.latest_version == entity.installed_version


def test_installed_version_unknown(coordinator, sample_data) -> None:
    """Test installed_version returns None when version is 'unknown'."""
    sample_data["hwinfo"]["version"] = "unknown"
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.installed_version is None
    assert entity.latest_version is None


def test_release_url(coordinator) -> None:
    """Test release_url points to the OMV update management page."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.release_url == "http://192.168.1.10:80/#/system/updatemgmt/updates"


def test_supported_features_includes_install(coordinator) -> None:
    """Test that UpdateEntityFeature.INSTALL is declared."""
    entity = OMVUpdateEntity(coordinator)
    assert entity._attr_supported_features & UpdateEntityFeature.INSTALL


@pytest.mark.asyncio
async def test_async_install_calls_apt_upgrade(coordinator) -> None:
    """Test async_install sets in_progress, triggers Apt.upgrade, and spawns poll task."""
    coordinator.api.async_call = AsyncMock(return_value=None)
    coordinator.async_request_refresh = AsyncMock()

    entity = OMVUpdateEntity(coordinator)
    entity.async_write_ha_state = MagicMock()
    spawned: list = []
    entity.hass = MagicMock()
    entity.hass.async_create_task = lambda coro: spawned.append(coro)

    await entity.async_install(version=None, backup=False)

    # Apt.upgrade must be called
    coordinator.api.async_call.assert_awaited_once_with("Apt", "upgrade")
    # in_progress must be True before the RPC returns
    assert entity._attr_in_progress is True
    # A background poll task must be scheduled
    assert len(spawned) == 1
    # Clean up the unawaited coroutine to avoid ResourceWarning
    spawned[0].close()


@pytest.mark.asyncio
async def test_async_install_clears_in_progress_on_rpc_error(coordinator) -> None:
    """Test async_install clears in_progress when Apt.upgrade raises."""
    coordinator.api.async_call = AsyncMock(side_effect=Exception("boom"))

    entity = OMVUpdateEntity(coordinator)
    entity.async_write_ha_state = MagicMock()

    with pytest.raises(Exception, match="boom"):
        await entity.async_install(version=None, backup=False)

    assert entity._attr_in_progress is False


@pytest.mark.asyncio
async def test_poll_install_completion_stops_when_updates_gone(coordinator, sample_data) -> None:
    """Test _async_poll_install_completion clears in_progress once updates reach 0."""
    call_count = 0

    async def _refresh() -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            coordinator.data["hwinfo"]["availablePkgUpdates"] = 0

    coordinator.async_request_refresh = _refresh

    entity = OMVUpdateEntity(coordinator)
    entity._attr_in_progress = True
    entity.async_write_ha_state = MagicMock()

    with patch("custom_components.omv.update.asyncio.sleep", new_callable=AsyncMock):
        await entity._async_poll_install_completion()

    assert entity._attr_in_progress is False
    assert call_count == 2
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_poll_install_completion_clears_after_timeout(coordinator, sample_data) -> None:
    """Test _async_poll_install_completion clears in_progress after max polls."""
    coordinator.async_request_refresh = AsyncMock()
    # availablePkgUpdates stays at 3 — never goes to 0

    entity = OMVUpdateEntity(coordinator)
    entity._attr_in_progress = True
    entity.async_write_ha_state = MagicMock()

    with (
        patch("custom_components.omv.update._MAX_POLLS", 2),
        patch("custom_components.omv.update.asyncio.sleep", new_callable=AsyncMock),
    ):
        await entity._async_poll_install_completion()

    assert entity._attr_in_progress is False


def test_get_expected_update_unique_ids(config_entry) -> None:
    """Test get_expected_update_unique_ids returns the correct unique ID set."""
    result = get_expected_update_unique_ids(config_entry)
    assert result == {f"{config_entry.entry_id}-omv_system_update"}


def test_unique_id(coordinator, config_entry) -> None:
    """Test unique_id is scoped to the config entry."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.unique_id == f"{config_entry.entry_id}-omv_system_update"


def test_device_info_is_hub_device(coordinator, config_entry) -> None:
    """Test that the entity is attached to the hub device, not a disk."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.device_info is not None
    assert (DOMAIN, config_entry.entry_id) in entity.device_info["identifiers"]


def test_title(coordinator) -> None:
    """Test title is set to 'OpenMediaVault'."""
    entity = OMVUpdateEntity(coordinator)
    assert entity._attr_title == "OpenMediaVault"


def test_suggested_object_id(coordinator) -> None:
    """Test suggested_object_id includes hostname and system_update."""
    entity = OMVUpdateEntity(coordinator)
    assert "nas" in entity._attr_suggested_object_id
    assert "system_update" in entity._attr_suggested_object_id

"""Tests for OMV update entity."""

from __future__ import annotations

import pytest

from custom_components.omv.const import DOMAIN
from custom_components.omv.update import OMVUpdateEntity, async_setup_entry


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
    """Test release_url points to the OMV web interface."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.release_url == "http://192.168.1.10:80"


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

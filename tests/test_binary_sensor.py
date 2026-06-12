"""Tests for OMV binary sensors."""

from __future__ import annotations

import pytest

from custom_components.omv.binary_sensor import OMVBinarySensor, async_setup_entry
from custom_components.omv.binary_sensor_types import (
    DISK_BAD_SECTORS_BINARY_SENSOR,
    DISK_CRC_ERRORS_BINARY_SENSOR,
    RSYNC_JOB_ENABLED_BINARY_SENSOR,
    SERVICE_BINARY_SENSOR,
    SYSTEM_BINARY_SENSORS,
    UPS_ON_BATTERY_BINARY_SENSOR,
    VM_RUNNING_BINARY_SENSOR,
)
from custom_components.omv.const import DOMAIN


@pytest.mark.asyncio
async def test_async_setup_entry_adds_binary_sensors(coordinator, config_entry) -> None:
    """Test binary sensor platform setup."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert any(entity.unique_id.endswith("service-ssh") for entity in added)
    assert any(entity.unique_id.endswith("service-compose") for entity in added)
    assert any(entity.unique_id.endswith("vm_running-vm-uuid-1234") for entity in added)
    assert any(entity.unique_id.endswith("ups_on_battery") for entity in added)
    assert any(entity.unique_id.endswith("rsync_job_enabled-rsync-uuid-0001") for entity in added)
    assert any(entity.unique_id.endswith("rsync_job_enabled-rsync-uuid-0002") for entity in added)


@pytest.mark.asyncio
async def test_ups_on_battery_absent_without_nut_data(coordinator, config_entry) -> None:
    """Test no UPS binary sensor is created when the nut dict is empty."""
    coordinator.data["nut"] = {}
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(entity.unique_id.endswith("ups_on_battery") for entity in added)


@pytest.mark.asyncio
async def test_ups_on_battery_binary_sensor_state_and_hub_device(coordinator) -> None:
    """Test the UPS on-battery sensor reads on_battery and stays on the hub device."""
    sensor = OMVBinarySensor(coordinator, UPS_ON_BATTERY_BINARY_SENSOR)

    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {"status": "OL", "model": "Eaton 5E"}
    assert sensor.device_info["identifiers"] == {(DOMAIN, coordinator.config_entry.entry_id)}
    assert sensor._attr_suggested_object_id == "nas_ups_on_battery"


@pytest.mark.asyncio
async def test_rsync_job_enabled_binary_sensor_state_and_attrs(coordinator) -> None:
    """Test the rsync job sensor reads enabled and exposes job attributes on the hub."""
    sensor = OMVBinarySensor(coordinator, RSYNC_JOB_ENABLED_BINARY_SENSOR, item_key="rsync-uuid-0001")

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "type": "local",
        "mode": "push",
        "srcname": "/srv/dev-disk-by-uuid-1/media",
        "destname": "/srv/dev-disk-by-uuid-2/backup",
        "schedule": "0 3 * * *",
        "uuid": "rsync-uuid-0001",
    }
    assert sensor.device_info["identifiers"] == {(DOMAIN, coordinator.config_entry.entry_id)}
    assert sensor._attr_suggested_object_id == "nas_rsync_backup_media_enabled"


@pytest.mark.asyncio
async def test_rsync_job_enabled_binary_sensor_disabled_job(coordinator) -> None:
    """Test a disabled rsync job reports off."""
    sensor = OMVBinarySensor(coordinator, RSYNC_JOB_ENABLED_BINARY_SENSOR, item_key="rsync-uuid-0002")

    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_rsync_binary_sensors_absent_without_jobs(coordinator, config_entry) -> None:
    """Test no rsync binary sensors are created when no jobs exist."""
    coordinator.data["rsync"] = []
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any("rsync_job_enabled" in entity.unique_id for entity in added)


@pytest.mark.asyncio
async def test_system_binary_sensor_state(coordinator) -> None:
    """Test singleton binary sensors use hwinfo flags."""
    sensor = OMVBinarySensor(coordinator, SYSTEM_BINARY_SENSORS[0])

    assert sensor.is_on is False  # rebootRequired defaults to False in sample_data
    assert sensor._attr_suggested_object_id == "nas_reboot_required"


@pytest.mark.asyncio
async def test_service_binary_sensor_attributes_and_hub_device(coordinator) -> None:
    """Test service binary sensor attributes stay on the hub device."""
    sensor = OMVBinarySensor(coordinator, SERVICE_BINARY_SENSOR, item_key="ssh")

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {"name": "ssh", "enabled": True}
    assert sensor.device_info["identifiers"] == {(DOMAIN, coordinator.config_entry.entry_id)}
    assert sensor._attr_suggested_object_id == "nas_service_ssh"


@pytest.mark.asyncio
async def test_compose_service_binary_sensor_includes_container_counts(coordinator) -> None:
    """Test Docker service sensors expose aggregated container counts."""
    sensor = OMVBinarySensor(coordinator, SERVICE_BINARY_SENSOR, item_key="compose")

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "name": "compose",
        "enabled": True,
        "container_total": 4,
        "container_running": 3,
        "container_not_running": 1,
    }


@pytest.mark.asyncio
async def test_vm_running_binary_sensor_state_and_device(coordinator) -> None:
    """Test VM running binary sensor reflects state and attaches to a VM device."""
    sensor = OMVBinarySensor(coordinator, VM_RUNNING_BINARY_SENSOR, item_key="vm-uuid-1234")

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {"state": "running", "autostart": True}
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:vm:vm-uuid-1234")}
    assert sensor._attr_suggested_object_id == "nas_vm_homeassistant_running"


@pytest.mark.asyncio
async def test_async_setup_entry_adds_smart_health_binary_sensors(coordinator, config_entry) -> None:
    """Test SMART health binary sensors are created only for SMART-eligible disks."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    unique_ids = {entity.unique_id for entity in added}
    entry_id = coordinator.config_entry.entry_id

    assert f"{entry_id}-disk_bad_sectors-sda" in unique_ids
    assert f"{entry_id}-disk_crc_errors-sda" in unique_ids
    assert f"{entry_id}-disk_bad_sectors-sdb" in unique_ids
    assert f"{entry_id}-disk_bad_sectors-md0" not in unique_ids
    assert f"{entry_id}-disk_crc_errors-md0" not in unique_ids


@pytest.mark.asyncio
async def test_disk_bad_sectors_sensor_reports_no_problem(coordinator) -> None:
    """Test the bad sectors sensor reports no problem when the SMART counter is zero."""
    sensor = OMVBinarySensor(coordinator, DISK_BAD_SECTORS_BINARY_SENSOR, item_key="sda")

    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {"reallocated_sector_ct": "0"}
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sda")}


@pytest.mark.asyncio
async def test_disk_crc_errors_sensor_detects_nonzero_counter(coordinator) -> None:
    """Test the CRC errors sensor reports a problem for a non-zero counter."""
    for disk in coordinator.data["disk"]:
        if disk.get("disk_key") == "sda":
            disk["UDMA_CRC_Error_Count"] = "3"

    sensor = OMVBinarySensor(coordinator, DISK_CRC_ERRORS_BINARY_SENSOR, item_key="sda")

    assert sensor.is_on is True


@pytest.mark.asyncio
async def test_smart_health_sensors_unknown_when_attribute_missing(coordinator) -> None:
    """Test SMART health sensors stay unknown when the raw attribute is unavailable."""
    bad_sectors = OMVBinarySensor(coordinator, DISK_BAD_SECTORS_BINARY_SENSOR, item_key="sdb")
    crc_errors = OMVBinarySensor(coordinator, DISK_CRC_ERRORS_BINARY_SENSOR, item_key="sdb")

    assert bad_sectors.is_on is None
    assert crc_errors.is_on is None
    assert bad_sectors.extra_state_attributes == {}


@pytest.mark.asyncio
async def test_virtual_disk_has_no_smart_health_binary_sensors(coordinator, config_entry) -> None:
    """Test virtual disks get no SMART health binary sensors."""
    for disk in coordinator.data.get("disk", []):
        if disk.get("disk_key") == "sda":
            disk["is_virtual"] = True

    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(entity.unique_id.endswith("disk_bad_sectors-sda") for entity in added)
    assert not any(entity.unique_id.endswith("disk_crc_errors-sda") for entity in added)

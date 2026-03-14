"""Tests for OMV sensors."""

from __future__ import annotations

import pytest

from custom_components.omv.sensor import OMVSensor, async_setup_entry
from custom_components.omv.sensor_types import DISK_SENSOR, SYSTEM_SENSORS, ZFS_POOL_SENSOR


@pytest.mark.asyncio
async def test_async_setup_entry_adds_expected_sensors(coordinator, config_entry) -> None:
    """Test sensor platform setup creates system and collection sensors."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert any(entity.unique_id.endswith("cpu_utilization") for entity in added)
    assert any(entity.unique_id.endswith("disk-sda") for entity in added)
    assert any(entity.unique_id.endswith("zfs_pool-tank") for entity in added)


@pytest.mark.asyncio
async def test_system_sensor_reads_native_value(coordinator) -> None:
    """Test a singleton system sensor exposes the coordinator value."""
    sensor = OMVSensor(coordinator, SYSTEM_SENSORS[0])

    assert sensor.native_value == 15.3
    assert sensor.extra_state_attributes == {
        "load_average_1min": 0.1,
        "load_average_5min": 0.2,
        "load_average_15min": 0.3,
    }


@pytest.mark.asyncio
async def test_disk_sensor_exposes_smart_attributes(coordinator) -> None:
    """Test disk sensors include SMART related attributes."""
    sensor = OMVSensor(coordinator, DISK_SENSOR, item_key="sda")

    assert sensor.native_value == 34.0
    assert sensor.extra_state_attributes["overall_status"] == "PASSED"
    assert sensor.extra_state_attributes["raw_read_error_rate"] == "0"


@pytest.mark.asyncio
async def test_zfs_sensor_uses_pool_state(coordinator) -> None:
    """Test the optional ZFS pool sensor."""
    sensor = OMVSensor(coordinator, ZFS_POOL_SENSOR, item_key="tank")

    assert sensor.native_value == "ONLINE"
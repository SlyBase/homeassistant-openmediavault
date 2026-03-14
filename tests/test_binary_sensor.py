"""Tests for OMV binary sensors."""

from __future__ import annotations

import pytest

from custom_components.omv.binary_sensor import OMVBinarySensor, async_setup_entry
from custom_components.omv.binary_sensor_types import SERVICE_BINARY_SENSOR, SYSTEM_BINARY_SENSORS


@pytest.mark.asyncio
async def test_async_setup_entry_adds_binary_sensors(coordinator, config_entry) -> None:
    """Test binary sensor platform setup."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert any(entity.unique_id.endswith("update_available") for entity in added)
    assert any(entity.unique_id.endswith("service-ssh") for entity in added)


@pytest.mark.asyncio
async def test_system_binary_sensor_state(coordinator) -> None:
    """Test singleton binary sensors use hwinfo flags."""
    sensor = OMVBinarySensor(coordinator, SYSTEM_BINARY_SENSORS[0])

    assert sensor.is_on is True


@pytest.mark.asyncio
async def test_service_binary_sensor_attributes(coordinator) -> None:
    """Test service binary sensor attributes."""
    sensor = OMVBinarySensor(coordinator, SERVICE_BINARY_SENSOR, item_key="ssh")

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {"name": "ssh", "enabled": True}
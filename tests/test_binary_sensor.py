"""Tests for OMV binary sensors."""

from __future__ import annotations

import pytest

from custom_components.omv.binary_sensor import OMVBinarySensor, async_setup_entry
from custom_components.omv.binary_sensor_types import (
    SERVICE_BINARY_SENSOR,
    SYSTEM_BINARY_SENSORS,
)
from custom_components.omv.const import DOMAIN


@pytest.mark.asyncio
async def test_async_setup_entry_adds_binary_sensors(coordinator, config_entry) -> None:
    """Test binary sensor platform setup."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert any(entity.unique_id.endswith("update_available") for entity in added)
    assert any(entity.unique_id.endswith("service-ssh") for entity in added)
    assert any(entity.unique_id.endswith("service-compose") for entity in added)


@pytest.mark.asyncio
async def test_system_binary_sensor_state(coordinator) -> None:
    """Test singleton binary sensors use hwinfo flags."""
    sensor = OMVBinarySensor(coordinator, SYSTEM_BINARY_SENSORS[0])

    assert sensor.is_on is True
    assert sensor._attr_suggested_object_id == "nas_update_available"


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

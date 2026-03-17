"""Tests for OMV sensors."""

from __future__ import annotations

import pytest

from custom_components.omv.const import DOMAIN
from custom_components.omv.sensor import OMVSensor, async_setup_entry
from custom_components.omv.sensor_types import (
    COMPOSE_SENSORS,
    COMPOSE_PROJECT_SENSORS,
    CONTAINER_SENSORS,
    CONTAINER_VOLUME_SENSORS,
    DISK_FREE_PERCENT_SENSOR,
    DISK_FREE_SIZE_SENSOR,
    DISK_SENSOR,
    DISK_TOTAL_SIZE_SENSOR,
    DISK_USED_PERCENT_SENSOR,
    DISK_USED_SIZE_SENSOR,
    FILESYSTEM_FREE_PERCENT_SENSOR,
    FILESYSTEM_FREE_SIZE_SENSOR,
    FILESYSTEM_SENSOR,
    FILESYSTEM_TOTAL_SIZE_SENSOR,
    FILESYSTEM_USED_SIZE_SENSOR,
    SYSTEM_SENSORS,
    RAID_SENSOR,
    ZFS_POOL_SENSOR,
)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_expected_sensors(coordinator, config_entry) -> None:
    """Test sensor platform setup creates system and collection sensors."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert any(entity.unique_id.endswith("cpu_utilization") for entity in added)
    assert any(entity.unique_id.endswith("available_package_updates") for entity in added)
    assert any(entity.unique_id.endswith("docker_container_total") for entity in added)
    assert any(entity.unique_id.endswith("compose_project_status-paperless") for entity in added)
    assert any(entity.unique_id.endswith("compose_project_total-paperless") for entity in added)
    assert any(entity.unique_id.endswith("container_state-ctr-paperless-app") for entity in added)
    assert any(entity.unique_id.endswith("container_volume_size-ctr-paperless-app:paperless_data") for entity in added)
    assert any(entity.unique_id.endswith("disk-sda") for entity in added)
    assert any(entity.unique_id.endswith("filesystem-fs-1") for entity in added)
    assert any(entity.unique_id.endswith("filesystem_free_percent-fs-1") for entity in added)
    assert any(entity.unique_id.endswith("zfs_pool-tank") for entity in added)
    assert not any(entity.unique_id.endswith("disk_used_size-sdb") for entity in added)


@pytest.mark.asyncio
async def test_system_sensor_reads_native_value(coordinator) -> None:
    """Test a singleton system sensor exposes the coordinator value."""
    sensor = OMVSensor(coordinator, SYSTEM_SENSORS[0])

    assert sensor.native_value == 15.3
    assert sensor.extra_state_attributes == {
        "cpu_model": "Intel(R) N100",
        "kernel": "Linux 6.6.0-omv",
        "load_average_1min": 0.1,
        "load_average_5min": 0.2,
        "load_average_15min": 0.3,
    }


@pytest.mark.asyncio
async def test_available_package_updates_sensor_exposes_numeric_count(coordinator) -> None:
    """Test the package update count sensor exposes the exact update count."""
    description = next(
        description
        for description in SYSTEM_SENSORS
        if description.key == "available_package_updates"
    )
    sensor = OMVSensor(coordinator, description)

    assert sensor.native_value == 3
    assert sensor._attr_suggested_object_id == "nas_available_package_updates"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (COMPOSE_SENSORS[0], 4),
        (COMPOSE_SENSORS[1], 3),
        (COMPOSE_SENSORS[2], 1),
    ],
)
async def test_docker_summary_sensors_expose_container_counts(
    coordinator, description, expected
) -> None:
    """Test dedicated Docker sensors expose summarized container counts."""
    sensor = OMVSensor(coordinator, description)

    assert sensor.native_value == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (COMPOSE_PROJECT_SENSORS[1], 2),
        (COMPOSE_PROJECT_SENSORS[2], 1),
        (COMPOSE_PROJECT_SENSORS[3], 1),
    ],
)
async def test_compose_project_sensors_expose_project_counts(
    coordinator, description, expected
) -> None:
    """Test compose project sensors expose grouped container counts."""
    sensor = OMVSensor(coordinator, description, item_key="paperless")

    assert sensor.native_value == expected


@pytest.mark.asyncio
async def test_compose_project_status_sensor_uses_stable_file_status(coordinator) -> None:
    """Test compose project status is exposed on the compose project, not the container."""
    sensor = OMVSensor(coordinator, COMPOSE_PROJECT_SENSORS[0], item_key="paperless")

    assert sensor.native_value == "UP"
    assert sensor.extra_state_attributes["uptime"] == "Up 5 minutes"


@pytest.mark.asyncio
async def test_container_sensors_use_container_device_and_project_parent(coordinator) -> None:
    """Test container sensors bind to container devices below compose projects."""
    sensor = OMVSensor(coordinator, CONTAINER_SENSORS[0], item_key="ctr-paperless-app")

    assert sensor.native_value == "running"
    assert sensor.device_info["identifiers"] == {
        (DOMAIN, f"{coordinator.config_entry.entry_id}:container:ctr-paperless-app")
    }
    assert sensor.device_info["via_device"] == (
        DOMAIN,
        f"{coordinator.config_entry.entry_id}:compose_project:paperless",
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_container_timestamp_sensors_expose_datetimes(coordinator) -> None:
    """Test container timestamp sensors keep normalized datetime values."""
    created = OMVSensor(coordinator, CONTAINER_SENSORS[1], item_key="ctr-paperless-app")
    started = OMVSensor(coordinator, CONTAINER_SENSORS[2], item_key="ctr-paperless-app")

    assert created.native_value == coordinator.data["compose"][0]["created_at"]
    assert started.native_value == coordinator.data["compose"][0]["started_at"]


@pytest.mark.asyncio
async def test_container_version_sensor_reads_opencontainers_label(coordinator) -> None:
    """Test container version sensor exposes org.opencontainers.image.version."""
    version = OMVSensor(coordinator, CONTAINER_SENSORS[3], item_key="ctr-paperless-app")

    assert version.native_value == coordinator.data["compose"][0]["version"]
    assert version.native_value == "2.15.3"


@pytest.mark.asyncio
async def test_disk_sensor_exposes_smart_attributes_and_disk_device_info(coordinator) -> None:
    """Test disk sensors include SMART related attributes and disk devices."""
    sensor = OMVSensor(coordinator, DISK_SENSOR, item_key="sda")

    assert sensor.native_value == 34.0
    assert sensor.extra_state_attributes["overall_status"] == "PASSED"
    assert sensor.extra_state_attributes["smart_attributes"] == {"Raw_Read_Error_Rate": "0"}
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sda")}
    assert sensor.device_info["via_device"] == (DOMAIN, coordinator.config_entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (DISK_USED_PERCENT_SENSOR, 40.0),
        (DISK_FREE_PERCENT_SENSOR, 60.0),
        (DISK_USED_SIZE_SENSOR, 40.0),
        (DISK_FREE_SIZE_SENSOR, 60.0),
        (DISK_TOTAL_SIZE_SENSOR, 100.0),
    ],
)
async def test_disk_capacity_sensors_use_projected_storage_metrics(
    coordinator, description, expected
) -> None:
    """Test disk entities expose projected capacity metrics on the disk device."""
    sensor = OMVSensor(coordinator, description, item_key="sda")

    assert sensor.native_value == expected


@pytest.mark.asyncio
async def test_filesystem_sensors_attach_to_disk_or_hub(coordinator) -> None:
    """Test filesystem sensors use disk devices when mapped and the hub otherwise."""
    mapped = OMVSensor(coordinator, FILESYSTEM_SENSOR, item_key="fs-1")
    unmapped = OMVSensor(coordinator, FILESYSTEM_SENSOR, item_key="fs-2")

    assert mapped.native_value == 40.0
    assert mapped.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sda")}
    assert unmapped.device_info["identifiers"] == {(DOMAIN, coordinator.config_entry.entry_id)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (FILESYSTEM_FREE_PERCENT_SENSOR, 60.0),
        (FILESYSTEM_USED_SIZE_SENSOR, 40.0),
        (FILESYSTEM_FREE_SIZE_SENSOR, 60.0),
        (FILESYSTEM_TOTAL_SIZE_SENSOR, 100.0),
    ],
)
async def test_additional_filesystem_metrics(coordinator, description, expected) -> None:
    """Test the additional filesystem metrics expose the expected values."""
    sensor = OMVSensor(coordinator, description, item_key="fs-1")

    assert sensor.native_value == expected


@pytest.mark.asyncio
async def test_zfs_sensor_uses_pool_state(coordinator) -> None:
    """Test the optional ZFS pool sensor."""
    sensor = OMVSensor(coordinator, ZFS_POOL_SENSOR, item_key="tank")

    assert sensor.native_value == "ONLINE"
    assert sensor.device_info["identifiers"] == {
        (DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sdc")
    }


@pytest.mark.asyncio
async def test_disk_free_percent_sensor_exposes_icon(coordinator) -> None:
    """Test disk free percentage sensors keep their icon metadata."""
    sensor = OMVSensor(coordinator, DISK_FREE_PERCENT_SENSOR, item_key="sda")

    assert sensor.icon == "mdi:harddisk"


@pytest.mark.asyncio
async def test_docker_not_running_sensor_exposes_icon(coordinator) -> None:
    """Test Docker not running summary keeps the docker-off icon."""
    sensor = OMVSensor(coordinator, COMPOSE_SENSORS[2])

    assert sensor.icon == "mdi:docker-off"


@pytest.mark.asyncio
async def test_container_volume_size_sensor_uses_container_device(coordinator) -> None:
    """Test volume size sensors attach to their container device."""
    sensor = OMVSensor(
        coordinator,
        CONTAINER_VOLUME_SENSORS[0],
        item_key="ctr-vaultwarden:vaultwarden_data",
    )

    assert sensor.native_value == 5.2
    assert sensor.device_info["identifiers"] == {
        (DOMAIN, f"{coordinator.config_entry.entry_id}:container:ctr-vaultwarden")
    }
    assert sensor.device_info["name"] == "Container vaultwarden"
    assert sensor.extra_state_attributes["destination"] == "/data"
    assert sensor._attr_suggested_object_id == (
        "nas_container_vaultwarden_volume_vaultwarden_data_volume_size"
    )


@pytest.mark.asyncio
async def test_collection_sensors_use_translation_placeholders(coordinator) -> None:
    """Test collection sensors use translated names with placeholders."""
    sensor = OMVSensor(coordinator, DISK_TOTAL_SIZE_SENSOR, item_key="sda")

    assert getattr(sensor, "_attr_name", None) is None
    assert sensor._attr_translation_placeholders == {"resource": "sda"}
    assert sensor._attr_suggested_object_id == "nas_disk_sda_total_size"


@pytest.mark.asyncio
async def test_raid_sensor_reports_health_value(coordinator) -> None:
    """Test RAID sensors expose a non-empty health state."""
    sensor = OMVSensor(coordinator, RAID_SENSOR, item_key="md0")

    assert sensor.native_value == "clean"


@pytest.mark.asyncio
async def test_virtual_passthrough_hides_temperature_entities(coordinator, config_entry) -> None:
    """Test virtual passthrough removes CPU and disk temperature entities."""
    coordinator.virtual_passthrough = True
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(entity.unique_id.endswith("cpu_temperature") for entity in added)
    assert not any(entity.unique_id.endswith("disk-sda") for entity in added)
    assert any(entity.unique_id.endswith("disk_used_size-sda") for entity in added)
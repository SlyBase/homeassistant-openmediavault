"""Tests for OMV sensors."""

from __future__ import annotations

import pytest
from homeassistant.const import EntityCategory

from custom_components.omv.const import DOMAIN
from custom_components.omv.sensor import OMVSensor, async_setup_entry
from custom_components.omv.sensor_types import (
    COMPOSE_PROJECT_SENSORS,
    COMPOSE_SENSORS,
    CONTAINER_SENSORS,
    CONTAINER_VOLUME_SENSORS,
    DISK_DATA_READ_SENSOR,
    DISK_DATA_WRITTEN_SENSOR,
    DISK_FREE_PERCENT_SENSOR,
    DISK_FREE_SIZE_SENSOR,
    DISK_SENSOR,
    DISK_TOTAL_SIZE_SENSOR,
    DISK_USED_PERCENT_SENSOR,
    DISK_USED_SIZE_SENSOR,
    DISK_WEAR_LEVEL_SENSOR,
    FILESYSTEM_FREE_PERCENT_SENSOR,
    FILESYSTEM_FREE_SIZE_SENSOR,
    FILESYSTEM_SENSOR,
    FILESYSTEM_TOTAL_SIZE_SENSOR,
    FILESYSTEM_USED_SIZE_SENSOR,
    RAID_SENSOR,
    SYSTEM_SENSORS,
    VM_SENSORS,
    ZFS_DATASET_SENSORS,
    ZFS_POOL_EXTRA_SENSORS,
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
    assert any(entity.unique_id.endswith("vm_state-vm-uuid-1234") for entity in added)
    assert any(entity.unique_id.endswith("ups_battery_charge") for entity in added)
    assert any(entity.unique_id.endswith("ups_battery_runtime") for entity in added)
    assert any(entity.unique_id.endswith("ups_load") for entity in added)
    assert not any(entity.unique_id.endswith("disk_used_size-sdb") for entity in added)


@pytest.mark.asyncio
async def test_ups_sensors_absent_without_nut_data(coordinator, config_entry) -> None:
    """Test no UPS sensors are created when the nut dict is empty."""
    coordinator.data["nut"] = {}
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(entity.unique_id.endswith("ups_battery_charge") for entity in added)
    assert not any(entity.unique_id.endswith("ups_battery_runtime") for entity in added)
    assert not any(entity.unique_id.endswith("ups_load") for entity in added)


@pytest.mark.asyncio
async def test_ups_battery_charge_sensor_exposes_value_and_attrs(coordinator, config_entry) -> None:
    """Test the UPS battery charge sensor reads value and status/model attributes."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    sensor = next(e for e in added if e.unique_id.endswith("ups_battery_charge"))
    assert sensor.native_value == 100.0
    assert sensor.extra_state_attributes == {"status": "OL", "model": "Eaton 5E"}


@pytest.mark.asyncio
async def test_raid_backed_filesystem_omits_duplicate_disk_metrics(coordinator, config_entry) -> None:
    """Mounted md devices should expose filesystem metrics without duplicate disk sizes."""
    coordinator.data["disk"].append(
        {
            "disk_key": "md127",
            "devicename": "md127",
            "devicefile": "/dev/md127",
            "canonicaldevicefile": "/dev/md127",
            "temperature": None,
            "model": "Linux MD RAID",
            "serialnumber": "md127",
            "size": "2000 GB",
            "total_size_gb": 2000.0,
            "used_size_gb": 1000.0,
            "free_size_gb": 1000.0,
            "used_percentage": 50.0,
            "free_percentage": 50.0,
            "storage_source": "filesystem",
            "storage_label": "SaveData",
            "vendor": "unknown",
            "overallstatus": "PASSED",
            "israid": True,
            "is_logical": True,
            "raid_level": "raid1",
        }
    )
    coordinator.data["fs"].append(
        {
            "uuid": "fs-md127",
            "label": "SaveData",
            "type": "ext4",
            "devicename": "md127",
            "devicefile": "/dev/md127",
            "canonicaldevicefile": "/dev/md127",
            "parentdevicefile": "/dev/md127",
            "disk_key": "md127",
            "size": 2000.0,
            "used": 1000.0,
            "available": 1000.0,
            "percentage": 50.0,
            "free_percentage": 50.0,
            "mountdir": "/srv/dev-disk-by-uuid-fs-md127",
        }
    )
    coordinator.data["raid"].append(
        {
            "device": "md127",
            "state": "active",
            "level": "raid1",
            "health": "clean",
            "health_indicator": "UU",
            "action_percent": None,
        }
    )
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    unique_ids = {entity.unique_id for entity in added}
    entry_id = coordinator.config_entry.entry_id

    assert f"{entry_id}-raid-md127" in unique_ids
    assert f"{entry_id}-filesystem-fs-md127" in unique_ids
    assert f"{entry_id}-filesystem_total_size-fs-md127" in unique_ids
    assert f"{entry_id}-disk_total_size-md127" not in unique_ids
    assert f"{entry_id}-disk_used_size-md127" not in unique_ids
    assert f"{entry_id}-disk_free_size-md127" not in unique_ids


@pytest.mark.asyncio
async def test_unmounted_md_raid_keeps_disk_metrics(coordinator, config_entry) -> None:
    """Unmounted md devices should keep disk capacity sensors for visibility."""
    coordinator.data["disk"].append(
        {
            "disk_key": "md1",
            "devicename": "md1",
            "devicefile": "/dev/md1",
            "canonicaldevicefile": "/dev/md1",
            "temperature": None,
            "model": "Linux MD RAID",
            "serialnumber": "md1",
            "size": "1000 GB",
            "total_size_gb": 1000.0,
            "used_size_gb": 250.0,
            "free_size_gb": 750.0,
            "used_percentage": 25.0,
            "free_percentage": 75.0,
            "storage_source": None,
            "storage_label": None,
            "vendor": "unknown",
            "overallstatus": "PASSED",
            "israid": True,
            "is_logical": True,
            "raid_level": "raid1",
        }
    )
    coordinator.data["raid"].append(
        {
            "device": "md1",
            "state": "active",
            "level": "raid1",
            "health": "clean",
            "health_indicator": "UU",
            "action_percent": None,
        }
    )
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    unique_ids = {entity.unique_id for entity in added}
    entry_id = coordinator.config_entry.entry_id

    assert f"{entry_id}-raid-md1" in unique_ids
    assert f"{entry_id}-disk_total_size-md1" in unique_ids
    assert f"{entry_id}-disk_used_size-md1" in unique_ids


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
    description = next(description for description in SYSTEM_SENSORS if description.key == "available_package_updates")
    sensor = OMVSensor(coordinator, description)

    assert sensor.native_value == 3
    assert sensor._attr_suggested_object_id == "nas_available_package_updates"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (SYSTEM_SENSORS[7], 0.1),
        (SYSTEM_SENSORS[8], 0.2),
        (SYSTEM_SENSORS[9], 0.3),
    ],
)
async def test_load_average_sensors_expose_values(coordinator, description, expected) -> None:
    """Test load average sensors expose the per-interval system load."""
    sensor = OMVSensor(coordinator, description)

    assert sensor.native_value == expected
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (COMPOSE_SENSORS[0], 4),
        (COMPOSE_SENSORS[1], 3),
        (COMPOSE_SENSORS[2], 1),
    ],
)
async def test_docker_summary_sensors_expose_container_counts(coordinator, description, expected) -> None:
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
async def test_compose_project_sensors_expose_project_counts(coordinator, description, expected) -> None:
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
    assert sensor.device_info["via_device_id"] == coordinator.project_device_ids["paperless"]


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
async def test_vm_state_sensor_exposes_state_and_device(coordinator) -> None:
    """Test VM state sensor exposes the normalized state and a dedicated VM device."""
    sensor = OMVSensor(coordinator, VM_SENSORS[0], item_key="vm-uuid-1234")

    assert sensor.native_value == "running"
    assert sensor.extra_state_attributes["memory"] == 2048.0
    assert sensor.extra_state_attributes["vcpu"] == 2.0
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:vm:vm-uuid-1234")}
    assert sensor.device_info["via_device_id"] == coordinator.hub_device_id


@pytest.mark.asyncio
async def test_disk_sensor_exposes_smart_attributes_and_disk_device_info(coordinator) -> None:
    """Test disk sensors include SMART related attributes and disk devices."""
    sensor = OMVSensor(coordinator, DISK_SENSOR, item_key="sda")

    assert sensor.native_value == 34.0
    assert sensor.extra_state_attributes["overall_status"] == "PASSED"
    assert sensor.extra_state_attributes["smart_attributes"] == {"Raw_Read_Error_Rate": "0"}
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sda")}
    assert sensor.device_info["via_device_id"] == coordinator.hub_device_id


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
async def test_disk_capacity_sensors_use_projected_storage_metrics(coordinator, description, expected) -> None:
    """Test disk entities expose projected capacity metrics on the disk device."""
    sensor = OMVSensor(coordinator, description, item_key="sda")

    assert sensor.native_value == expected


@pytest.mark.asyncio
async def test_filesystem_sensors_attach_to_disk_or_hub(coordinator) -> None:
    """Test filesystem sensors use disk devices when mapped, standalone filesystem devices otherwise."""
    mapped = OMVSensor(coordinator, FILESYSTEM_SENSOR, item_key="fs-1")
    unmapped = OMVSensor(coordinator, FILESYSTEM_SENSOR, item_key="fs-2")

    assert mapped.native_value == 40.0
    assert mapped.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sda")}
    assert unmapped.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:filesystem:fs-2")}


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
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sdc")}


@pytest.mark.asyncio
async def test_disk_free_percent_sensor_exposes_icon(coordinator) -> None:
    """Test disk free percentage sensors keep their icon metadata."""
    sensor = OMVSensor(coordinator, DISK_FREE_PERCENT_SENSOR, item_key="sda")

    assert sensor.icon == "mdi:harddisk"


@pytest.mark.asyncio
async def test_docker_not_running_sensor_exposes_icon(coordinator) -> None:
    """Test Docker not running summary exposes the docker icon."""
    sensor = OMVSensor(coordinator, COMPOSE_SENSORS[2])

    assert sensor.icon == "mdi:docker"


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
    assert sensor._attr_suggested_object_id == ("nas_container_vaultwarden_volume_vaultwarden_data_volume_size")


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
async def test_raid_disk_entries_do_not_generate_disk_sensors(coordinator, config_entry) -> None:
    """Test that synthetic RAID entries in coordinator.data['disk'] produce no disk-style sensors.

    When OMV does not list md* devices in DiskMgmt.enumerateDevices, the coordinator
    synthesises logical disk records (israid=True, is_logical=True) so that filesystem
    metrics can be projected onto the RAID device.  These synthetic records must NOT
    produce disk-capacity sensors (used_percent, total_size, …) — only the dedicated
    RAID health sensor (from coordinator.data["raid"]) must appear.
    """
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    # RAID health sensor must still be created
    assert any(entity.unique_id.endswith("raid-md0") for entity in added)
    # No disk-capacity / temperature sensors for the synthetic RAID disk entry
    for suffix in (
        "disk-md0",
        "disk_used_percent-md0",
        "disk_free_percent-md0",
        "disk_used_size-md0",
        "disk_free_size-md0",
        "disk_total_size-md0",
    ):
        assert not any(entity.unique_id.endswith(suffix) for entity in added), (
            f"Unexpected disk sensor with suffix '{suffix}' found for RAID device md0"
        )


@pytest.mark.asyncio
async def test_virtual_disk_has_no_temperature_or_smart_entity(coordinator, config_entry) -> None:
    """Test that QEMU/virtual disks get no temperature or SMART Status entity."""
    for disk in coordinator.data.get("disk", []):
        if disk.get("disk_key") == "sda":
            disk["is_virtual"] = True
            disk["temperature"] = None
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(entity.unique_id.endswith("disk-sda") for entity in added)
    assert not any(entity.unique_id.endswith("disk_smart_status-sda") for entity in added)
    assert any(entity.unique_id.endswith("disk_used_size-sda") for entity in added)


@pytest.mark.asyncio
async def test_wear_sensors_created_only_for_disks_with_values(coordinator, config_entry) -> None:
    """Wear/data sensors appear for SSD/NVMe samples only, never for HDDs or RAID (#54)."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    for disk_key in ("nvme0n1", "sdd"):
        for metric in ("disk_wear_level", "disk_data_written", "disk_data_read"):
            assert any(entity.unique_id.endswith(f"{metric}-{disk_key}") for entity in added), (
                f"missing {metric} for {disk_key}"
            )
    # HDDs without wear data and the synthetic RAID record get none.
    for disk_key in ("sda", "sdb", "sdc", "md0"):
        for metric in ("disk_wear_level", "disk_data_written", "disk_data_read"):
            assert not any(entity.unique_id.endswith(f"{metric}-{disk_key}") for entity in added), (
                f"unexpected {metric} for {disk_key}"
            )


@pytest.mark.asyncio
async def test_nvme_wear_sensor_exposes_value_and_health_attributes(coordinator) -> None:
    """The NVMe wear sensor reports percentage used with the health log as attributes (#54)."""
    sensor = OMVSensor(coordinator, DISK_WEAR_LEVEL_SENSOR, item_key="nvme0n1")

    assert sensor.native_value == 98.0
    assert sensor.extra_state_attributes["nvme_health"]["data_units_written"] == 1892791876
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:nvme0n1")}
    assert sensor._attr_suggested_object_id == "nas_disk_nvme0n1_wear_level"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "item_key", "expected"),
    [
        (DISK_DATA_WRITTEN_SENSOR, "nvme0n1", 969.11),
        (DISK_DATA_READ_SENSOR, "nvme0n1", 299.87),
        (DISK_WEAR_LEVEL_SENSOR, "sdd", 3.0),
        (DISK_DATA_WRITTEN_SENSOR, "sdd", 40.02),
        (DISK_DATA_READ_SENSOR, "sdd", 20.01),
    ],
)
async def test_wear_and_data_sensors_expose_expected_values(coordinator, description, item_key, expected) -> None:
    """Wear/data sensors read the coordinator-derived disk fields (#54)."""
    sensor = OMVSensor(coordinator, description, item_key=item_key)

    assert sensor.native_value == expected


@pytest.mark.asyncio
async def test_async_setup_entry_adds_zfs_pool_and_dataset_sensors(coordinator, config_entry) -> None:
    """Test ZFS pool extra sensors and dataset sensors are created."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert any(entity.unique_id.endswith("zfs_pool_last_scrub-tank") for entity in added)
    assert any(entity.unique_id.endswith("zfs_pool_dataset_count-tank") for entity in added)
    assert any(entity.unique_id.endswith("zfs_pool_snapshot_count-tank") for entity in added)
    assert any(entity.unique_id.endswith("zfs_dataset_used-tank/media") for entity in added)
    assert any(entity.unique_id.endswith("zfs_dataset_used-tank/docs") for entity in added)


@pytest.mark.asyncio
async def test_zfs_pool_extra_sensors_expose_scrub_and_counts(coordinator) -> None:
    """Test pool-level scrub/count sensors read the enriched pool record."""
    last_scrub = OMVSensor(coordinator, ZFS_POOL_EXTRA_SENSORS[0], item_key="tank")
    dataset_count = OMVSensor(coordinator, ZFS_POOL_EXTRA_SENSORS[1], item_key="tank")
    snapshot_count = OMVSensor(coordinator, ZFS_POOL_EXTRA_SENSORS[2], item_key="tank")

    assert last_scrub.native_value == "Sun Jun  8 03:00:42 2026"
    assert last_scrub.entity_category is EntityCategory.DIAGNOSTIC
    assert last_scrub.extra_state_attributes["scrubstate"] == "completed"
    assert last_scrub._attr_suggested_object_id == "nas_zfs_tank_last_scrub"
    assert dataset_count.native_value == 2
    assert snapshot_count.native_value == 5


@pytest.mark.asyncio
async def test_zfs_dataset_sensor_uses_pool_device(coordinator) -> None:
    """Test dataset sensors expose usage and attach to the pool's disk device."""
    sensor = OMVSensor(coordinator, ZFS_DATASET_SENSORS[0], item_key="tank/media")

    assert sensor.native_value == 420.5
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{coordinator.config_entry.entry_id}:disk:sdc")}
    assert sensor.extra_state_attributes["pool"] == "tank"
    assert sensor.extra_state_attributes["mountpoint"] == "/srv/tank/media"
    assert sensor.extra_state_attributes["compression"] == "lz4"
    assert sensor.extra_state_attributes["available_gb"] == 579.5
    assert sensor._attr_translation_placeholders == {"resource": "tank/media"}
    assert sensor._attr_suggested_object_id == "nas_zfs_dataset_tank_media_used"


@pytest.mark.asyncio
async def test_expected_sensor_registry_state_includes_zfs_entities(coordinator) -> None:
    """Test the registry cleanup whitelist covers the new ZFS sensors."""
    from custom_components.omv.sensor import get_expected_sensor_registry_state

    unique_ids, _ = get_expected_sensor_registry_state(coordinator)
    entry_id = coordinator.config_entry.entry_id

    assert f"{entry_id}-zfs_pool_last_scrub-tank" in unique_ids
    assert f"{entry_id}-zfs_pool_dataset_count-tank" in unique_ids
    assert f"{entry_id}-zfs_pool_snapshot_count-tank" in unique_ids
    assert f"{entry_id}-zfs_dataset_used-tank/media" in unique_ids
    assert f"{entry_id}-zfs_dataset_used-tank/docs" in unique_ids


@pytest.mark.asyncio
async def test_zfs_pool_sensors_dedupe_across_disks(coordinator, config_entry) -> None:
    """A pool spanning multiple disks yields one entity per pool sensor."""
    pool_a = coordinator.data["zfs"][0]
    pool_b = {**pool_a, "disk_key": "sdb"}  # same name "tank", different disk
    coordinator.data = {**coordinator.data, "zfs": [pool_a, pool_b]}

    added: list = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    unique_ids = [entity.unique_id for entity in added]
    assert len(unique_ids) == len(set(unique_ids))
    for suffix in (
        "zfs_pool-tank",
        "zfs_pool_last_scrub-tank",
        "zfs_pool_dataset_count-tank",
        "zfs_pool_snapshot_count-tank",
    ):
        assert sum(uid.endswith(suffix) for uid in unique_ids) == 1, suffix

"""Sensor descriptions for the OpenMediaVault integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfTemperature,
)


@dataclass(frozen=True, kw_only=True)
class OMVSensorDescription(SensorEntityDescription):
    """Describe an OMV sensor."""

    data_path: str
    value_fn: Callable[[dict[str, Any]], Any]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    is_collection: bool = False
    collection_key: str | None = None
    name_key: str | None = None


def _filesystem_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return common filesystem attributes."""
    return {
        "label": data.get("label"),
        "type": data.get("type"),
        "devicefile": data.get("devicefile"),
        "canonicaldevicefile": data.get("canonicaldevicefile"),
        "parentdevicefile": data.get("parentdevicefile"),
        "mountpoint": data.get("mountdir"),
        "disk_key": data.get("disk_key"),
        "total": data.get("size"),
        "used": data.get("used"),
        "available": data.get("available"),
        "free_percentage": data.get("free_percentage"),
    }


def _disk_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return common disk attributes."""
    return {
        "disk_key": data.get("disk_key"),
        "devicefile": data.get("canonicaldevicefile") or data.get("devicefile"),
        "model": data.get("model"),
        "serial": data.get("serialnumber"),
        "size": data.get("size"),
        "vendor": data.get("vendor"),
        "overall_status": data.get("overallstatus"),
        "raid_level": data.get("raid_level"),
        "storage_source": data.get("storage_source"),
        "storage_label": data.get("storage_label"),
        "used_size": data.get("used_size_gb"),
        "free_size": data.get("free_size_gb"),
        "used_percentage": data.get("used_percentage"),
        "free_percentage": data.get("free_percentage"),
    }


SYSTEM_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="cpu_utilization",
        translation_key="cpu_utilization",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:cpu-64-bit",
        state_class=SensorStateClass.MEASUREMENT,
        data_path="hwinfo",
        value_fn=lambda data: data.get("cpuUtilization"),
        extra_attrs_fn=lambda data: {
            "cpu_model": data.get("cpuModel"),
            "kernel": data.get("kernel"),
            "load_average_1min": data.get("loadAverage", {}).get("1min"),
            "load_average_5min": data.get("loadAverage", {}).get("5min"),
            "load_average_15min": data.get("loadAverage", {}).get("15min"),
        },
    ),
    OMVSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:memory",
        state_class=SensorStateClass.MEASUREMENT,
        data_path="hwinfo",
        value_fn=lambda data: data.get("memUsage"),
        extra_attrs_fn=lambda data: {
            "memory_total": data.get("memTotal"),
            "memory_used": data.get("memUsed"),
        },
    ),
    OMVSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:thermometer",
        state_class=SensorStateClass.MEASUREMENT,
        data_path="hwinfo",
        value_fn=lambda data: data.get("cputemp"),
    ),
    OMVSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        data_path="hwinfo",
        value_fn=lambda data: data.get("uptimeEpoch"),
    ),
    OMVSensorDescription(
        key="available_package_updates",
        translation_key="available_package_updates",
        icon="mdi:package-up",
        data_path="hwinfo",
        value_fn=lambda data: data.get("availablePkgUpdates"),
        extra_attrs_fn=lambda data: {
            "pkg_updates_available": data.get("pkgUpdatesAvailable"),
        },
    ),
)

GPU_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="gpu_load",
        translation_key="gpu_load",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:expansion-card",
        state_class=SensorStateClass.MEASUREMENT,
        data_path="gpu",
        value_fn=lambda data: data.get("load_percent"),
        extra_attrs_fn=lambda data: {
            "vendor": data.get("vendor"),
            "model": data.get("model"),
            "cur_freq_mhz": data.get("cur_freq"),
            "max_freq_mhz": data.get("max_freq"),
        },
    ),
    OMVSensorDescription(
        key="gpu_cur_freq",
        translation_key="gpu_cur_freq",
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        icon="mdi:chip",
        state_class=SensorStateClass.MEASUREMENT,
        data_path="gpu",
        value_fn=lambda data: data.get("cur_freq"),
    ),
)


COMPOSE_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="docker_container_total",
        translation_key="docker_container_total",
        icon="mdi:docker",
        data_path="compose_summary",
        value_fn=lambda data: data.get("total"),
        extra_attrs_fn=lambda data: {
            "running": data.get("running"),
            "not_running": data.get("not_running"),
        },
    ),
    OMVSensorDescription(
        key="docker_container_running",
        translation_key="docker_container_running",
        icon="mdi:docker",
        data_path="compose_summary",
        value_fn=lambda data: data.get("running"),
        extra_attrs_fn=lambda data: {
            "total": data.get("total"),
            "not_running": data.get("not_running"),
        },
    ),
    OMVSensorDescription(
        key="docker_container_not_running",
        translation_key="docker_container_not_running",
        icon="mdi:docker-off",
        data_path="compose_summary",
        value_fn=lambda data: data.get("not_running"),
        extra_attrs_fn=lambda data: {
            "total": data.get("total"),
            "running": data.get("running"),
        },
    ),
)


def _compose_project_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return common compose project attributes."""
    return {
        "project": data.get("name"),
        "uuid": data.get("uuid"),
        "status": data.get("status"),
        "uptime": data.get("uptime"),
        "service": data.get("service_name"),
        "image": data.get("image"),
        "description": data.get("description"),
        "ports": data.get("ports"),
        "container_total": data.get("container_total"),
        "container_running": data.get("container_running"),
        "container_not_running": data.get("container_not_running"),
    }


def _container_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return common Docker container attributes."""
    return {
        "container_key": data.get("container_key"),
        "container_id": data.get("container_id"),
        "image": data.get("image"),
        "version": data.get("version"),
        "project": data.get("project_name"),
        "project_uuid": data.get("project_uuid"),
        "service": data.get("compose_service"),
        "state": data.get("state"),
        "status_detail": data.get("status_detail"),
        "project_status": data.get("project_status"),
        "project_uptime": data.get("project_uptime"),
        "running": data.get("running"),
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at"),
    }


def _container_volume_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return common Docker container volume attributes."""
    return {
        "volume": data.get("name"),
        "source": data.get("source"),
        "destination": data.get("destination"),
        "mountpoint": data.get("mountpoint"),
        "driver": data.get("driver"),
        "container": data.get("container_name"),
        "container_key": data.get("container_key"),
        "project": data.get("project_name"),
        "image": data.get("image"),
        "version": data.get("version"),
    }


COMPOSE_PROJECT_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="compose_project_status",
        translation_key="compose_project_status",
        icon="mdi:text-box-outline",
        data_path="compose_projects",
        is_collection=True,
        collection_key="project_key",
        name_key="name",
        value_fn=lambda data: data.get("status"),
        extra_attrs_fn=_compose_project_attrs,
    ),
    OMVSensorDescription(
        key="compose_project_total",
        translation_key="compose_project_total",
        icon="mdi:docker",
        data_path="compose_projects",
        is_collection=True,
        collection_key="project_key",
        name_key="name",
        value_fn=lambda data: data.get("container_total"),
        extra_attrs_fn=_compose_project_attrs,
    ),
    OMVSensorDescription(
        key="compose_project_running",
        translation_key="compose_project_running",
        icon="mdi:docker",
        data_path="compose_projects",
        is_collection=True,
        collection_key="project_key",
        name_key="name",
        value_fn=lambda data: data.get("container_running"),
        extra_attrs_fn=_compose_project_attrs,
    ),
    OMVSensorDescription(
        key="compose_project_not_running",
        translation_key="compose_project_not_running",
        icon="mdi:docker",
        data_path="compose_projects",
        is_collection=True,
        collection_key="project_key",
        name_key="name",
        value_fn=lambda data: data.get("container_not_running"),
        extra_attrs_fn=_compose_project_attrs,
    ),
)


CONTAINER_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="container_state",
        translation_key="container_state",
        icon="mdi:docker",
        data_path="compose",
        is_collection=True,
        collection_key="container_key",
        name_key="name",
        value_fn=lambda data: data.get("state"),
        extra_attrs_fn=_container_attrs,
    ),
    OMVSensorDescription(
        key="container_created_at",
        translation_key="container_created_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        data_path="compose",
        is_collection=True,
        collection_key="container_key",
        name_key="name",
        value_fn=lambda data: data.get("created_at"),
        extra_attrs_fn=_container_attrs,
    ),
    OMVSensorDescription(
        key="container_started_at",
        translation_key="container_started_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        data_path="compose",
        is_collection=True,
        collection_key="container_key",
        name_key="name",
        value_fn=lambda data: data.get("started_at"),
        extra_attrs_fn=_container_attrs,
    ),
    OMVSensorDescription(
        key="container_version",
        translation_key="container_version",
        icon="mdi:tag-outline",
        data_path="compose",
        is_collection=True,
        collection_key="container_key",
        name_key="name",
        value_fn=lambda data: data.get("version") or None,
        extra_attrs_fn=_container_attrs,
    ),
)


CONTAINER_VOLUME_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="container_volume_size",
        translation_key="container_volume_size",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        icon="mdi:database",
        state_class=SensorStateClass.MEASUREMENT,
        data_path="compose_volumes",
        is_collection=True,
        collection_key="volume_key",
        name_key="display_name",
        value_fn=lambda data: data.get("size_gb"),
        extra_attrs_fn=_container_volume_attrs,
    ),
)


DISK_SENSOR = OMVSensorDescription(
    key="disk",
    translation_key="disk_temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    icon="mdi:harddisk",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: data.get("temperature"),
    extra_attrs_fn=lambda data: {
        **_disk_attrs(data),
        "smart_details": data.get("smart_details"),
        "smart_attributes": data.get("smart_attributes"),
        "raw_read_error_rate": data.get("Raw_Read_Error_Rate"),
        "spin_up_time": data.get("Spin_Up_Time"),
        "start_stop_count": data.get("Start_Stop_Count"),
        "reallocated_sector_ct": data.get("Reallocated_Sector_Ct"),
        "seek_error_rate": data.get("Seek_Error_Rate"),
        "load_cycle_count": data.get("Load_Cycle_Count"),
        "udma_crc_error_count": data.get("UDMA_CRC_Error_Count"),
        "multi_zone_error_rate": data.get("Multi_Zone_Error_Rate"),
    },
)

DISK_USED_PERCENT_SENSOR = OMVSensorDescription(
    key="disk_used_percent",
    translation_key="disk_used_percent",
    native_unit_of_measurement=PERCENTAGE,
    icon="mdi:harddisk",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: data.get("used_percentage"),
    extra_attrs_fn=_disk_attrs,
)

DISK_FREE_PERCENT_SENSOR = OMVSensorDescription(
    key="disk_free_percent",
    translation_key="disk_free_percent",
    native_unit_of_measurement=PERCENTAGE,
    icon="mdi:harddisk",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: data.get("free_percentage"),
    extra_attrs_fn=_disk_attrs,
)

DISK_USED_SIZE_SENSOR = OMVSensorDescription(
    key="disk_used_size",
    translation_key="disk_used_size",
    native_unit_of_measurement=UnitOfInformation.GIGABYTES,
    icon="mdi:database-arrow-up",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: data.get("used_size_gb"),
    extra_attrs_fn=_disk_attrs,
)

DISK_FREE_SIZE_SENSOR = OMVSensorDescription(
    key="disk_free_size",
    translation_key="disk_free_size",
    native_unit_of_measurement=UnitOfInformation.GIGABYTES,
    icon="mdi:database-arrow-down",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: data.get("free_size_gb"),
    extra_attrs_fn=_disk_attrs,
)

DISK_TOTAL_SIZE_SENSOR = OMVSensorDescription(
    key="disk_total_size",
    translation_key="disk_total_size",
    native_unit_of_measurement=UnitOfInformation.GIGABYTES,
    icon="mdi:database",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: data.get("total_size_gb"),
    extra_attrs_fn=_disk_attrs,
)

FILESYSTEM_SENSOR = OMVSensorDescription(
    key="filesystem",
    translation_key="filesystem_used_percent",
    native_unit_of_measurement=PERCENTAGE,
    icon="mdi:harddisk",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="fs",
    is_collection=True,
    collection_key="uuid",
    name_key="label",
    value_fn=lambda data: data.get("percentage"),
    extra_attrs_fn=_filesystem_attrs,
)

FILESYSTEM_FREE_PERCENT_SENSOR = OMVSensorDescription(
    key="filesystem_free_percent",
    translation_key="filesystem_free_percent",
    native_unit_of_measurement=PERCENTAGE,
    icon="mdi:harddisk",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="fs",
    is_collection=True,
    collection_key="uuid",
    name_key="label",
    value_fn=lambda data: data.get("free_percentage"),
    extra_attrs_fn=_filesystem_attrs,
)

FILESYSTEM_USED_SIZE_SENSOR = OMVSensorDescription(
    key="filesystem_used_size",
    translation_key="filesystem_used_size",
    native_unit_of_measurement=UnitOfInformation.GIGABYTES,
    icon="mdi:database-arrow-up",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="fs",
    is_collection=True,
    collection_key="uuid",
    name_key="label",
    value_fn=lambda data: data.get("used"),
    extra_attrs_fn=_filesystem_attrs,
)

FILESYSTEM_FREE_SIZE_SENSOR = OMVSensorDescription(
    key="filesystem_free_size",
    translation_key="filesystem_free_size",
    native_unit_of_measurement=UnitOfInformation.GIGABYTES,
    icon="mdi:database-arrow-down",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="fs",
    is_collection=True,
    collection_key="uuid",
    name_key="label",
    value_fn=lambda data: data.get("available"),
    extra_attrs_fn=_filesystem_attrs,
)

FILESYSTEM_TOTAL_SIZE_SENSOR = OMVSensorDescription(
    key="filesystem_total_size",
    translation_key="filesystem_total_size",
    native_unit_of_measurement=UnitOfInformation.GIGABYTES,
    icon="mdi:database",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="fs",
    is_collection=True,
    collection_key="uuid",
    name_key="label",
    value_fn=lambda data: data.get("size"),
    extra_attrs_fn=_filesystem_attrs,
)

NETWORK_TX_SENSOR = OMVSensorDescription(
    key="network_tx",
    translation_key="network_tx",
    native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
    icon="mdi:upload-network-outline",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="network",
    is_collection=True,
    collection_key="uuid",
    name_key="devicename",
    value_fn=lambda data: data.get("tx"),
    extra_attrs_fn=lambda data: {
        "type": data.get("type"),
        "method": data.get("method"),
        "address": data.get("address"),
        "netmask": data.get("netmask"),
        "gateway": data.get("gateway"),
        "mtu": data.get("mtu"),
        "link": data.get("link"),
        "wol": data.get("wol"),
    },
)

NETWORK_RX_SENSOR = OMVSensorDescription(
    key="network_rx",
    translation_key="network_rx",
    native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
    icon="mdi:download-network-outline",
    state_class=SensorStateClass.MEASUREMENT,
    data_path="network",
    is_collection=True,
    collection_key="uuid",
    name_key="devicename",
    value_fn=lambda data: data.get("rx"),
    extra_attrs_fn=lambda data: {
        "type": data.get("type"),
        "method": data.get("method"),
        "address": data.get("address"),
        "netmask": data.get("netmask"),
        "gateway": data.get("gateway"),
        "mtu": data.get("mtu"),
        "link": data.get("link"),
        "wol": data.get("wol"),
    },
)

RAID_SENSOR = OMVSensorDescription(
    key="raid",
    translation_key="raid",
    icon="mdi:harddisk-plus",
    data_path="raid",
    is_collection=True,
    collection_key="device",
    name_key="device",
    value_fn=lambda data: data.get("health"),
    extra_attrs_fn=lambda data: {
        "devicefile": data.get("devicefile"),
        "disk_key": data.get("disk_key"),
        "health": data.get("health"),
        "state": data.get("state"),
        "level": data.get("level"),
        "health_indicator": data.get("health_indicator"),
        "action_percent": data.get("action_percent"),
    },
)

ZFS_POOL_SENSOR = OMVSensorDescription(
    key="zfs_pool",
    translation_key="zfs_pool",
    icon="mdi:database-cog",
    data_path="zfs",
    is_collection=True,
    collection_key="name",
    name_key="name",
    value_fn=lambda data: data.get("state") or data.get("health"),
    extra_attrs_fn=lambda data: {
        "disk_key": data.get("disk_key"),
        "mountpoint": data.get("mountpoint"),
        "size": data.get("size"),
        "alloc": data.get("alloc"),
        "free": data.get("free"),
        "available": data.get("available"),
        "fragmentation": data.get("fragmentation"),
        "capacity": data.get("capacity"),
    },
)

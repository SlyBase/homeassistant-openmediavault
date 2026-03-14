"""Sensor descriptions for the OpenMediaVault integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfDataRate, UnitOfTemperature


@dataclass(frozen=True, kw_only=True)
class OMVSensorDescription(SensorEntityDescription):
    """Describe an OMV sensor."""

    data_path: str
    value_fn: Callable[[dict[str, Any]], Any]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    is_collection: bool = False
    collection_key: str | None = None
    name_key: str | None = None


SYSTEM_SENSORS: tuple[OMVSensorDescription, ...] = (
    OMVSensorDescription(
        key="cpu_utilization",
        translation_key="cpu_utilization",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        data_path="hwinfo",
        value_fn=lambda data: data.get("cpuUtilization"),
        extra_attrs_fn=lambda data: {
            "load_average_1min": data.get("loadAverage", {}).get("1min"),
            "load_average_5min": data.get("loadAverage", {}).get("5min"),
            "load_average_15min": data.get("loadAverage", {}).get("15min"),
        },
    ),
    OMVSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        data_path="hwinfo",
        value_fn=lambda data: data.get("memUsage"),
    ),
    OMVSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        data_path="hwinfo",
        value_fn=lambda data: data.get("cputemp"),
    ),
    OMVSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        data_path="hwinfo",
        value_fn=lambda data: data.get("uptimeEpoch"),
    ),
)

FILESYSTEM_SENSOR = OMVSensorDescription(
    key="filesystem",
    name="Filesystem",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    data_path="fs",
    is_collection=True,
    collection_key="uuid",
    name_key="label",
    value_fn=lambda data: data.get("percentage"),
    extra_attrs_fn=lambda data: {
        "label": data.get("label"),
        "type": data.get("type"),
        "total": data.get("size"),
        "used": data.get("used"),
        "available": data.get("available"),
        "mountpoint": data.get("mountdir"),
    },
)

DISK_SENSOR = OMVSensorDescription(
    key="disk",
    name="Temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    data_path="disk",
    is_collection=True,
    collection_key="devicename",
    name_key="devicename",
    value_fn=lambda data: data.get("temperature"),
    extra_attrs_fn=lambda data: {
        "model": data.get("model"),
        "serial": data.get("serialnumber"),
        "size": data.get("size"),
        "vendor": data.get("vendor"),
        "overall_status": data.get("overallstatus"),
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

NETWORK_TX_SENSOR = OMVSensorDescription(
    key="network_tx",
    name="TX",
    native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
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
    name="RX",
    native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
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
    name="RAID",
    data_path="raid",
    is_collection=True,
    collection_key="device",
    name_key="device",
    value_fn=lambda data: data.get("health"),
    extra_attrs_fn=lambda data: {
        "state": data.get("state"),
        "level": data.get("level"),
        "health_indicator": data.get("health_indicator"),
        "action_percent": data.get("action_percent"),
    },
)

ZFS_POOL_SENSOR = OMVSensorDescription(
    key="zfs_pool",
    name="ZFS Pool",
    data_path="zfs",
    is_collection=True,
    collection_key="name",
    name_key="name",
    value_fn=lambda data: data.get("state") or data.get("health"),
    extra_attrs_fn=lambda data: {
        "size": data.get("size"),
        "alloc": data.get("alloc"),
        "free": data.get("free"),
        "fragmentation": data.get("fragmentation"),
        "capacity": data.get("capacity"),
    },
)
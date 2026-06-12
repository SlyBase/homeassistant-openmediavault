"""Binary sensor descriptions for the OpenMediaVault integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .entity import disk_is_smart_eligible


@dataclass(frozen=True, kw_only=True)
class OMVBinarySensorDescription(BinarySensorEntityDescription):
    """Describe an OMV binary sensor."""

    data_path: str
    value_fn: Callable[[dict[str, Any]], bool | None]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    is_collection: bool = False
    collection_key: str | None = None
    name_key: str | None = None


def _smart_attribute_problem(data: dict[str, Any], attr_name: str) -> bool | None:
    """Return whether a SMART raw attribute is non-zero, or None if unavailable."""
    if not disk_is_smart_eligible(data):
        return None
    raw_value = data.get(attr_name)
    if raw_value is None or raw_value == "unknown":
        return None
    try:
        return float(raw_value) > 0
    except (TypeError, ValueError):
        return None


SYSTEM_BINARY_SENSORS: tuple[OMVBinarySensorDescription, ...] = (
    OMVBinarySensorDescription(
        key="reboot_required",
        translation_key="reboot_required",
        icon="mdi:restart-alert",
        data_path="hwinfo",
        value_fn=lambda data: bool(data.get("rebootRequired", False)),
    ),
)

SERVICE_BINARY_SENSOR = OMVBinarySensorDescription(
    key="service",
    translation_key="service",
    device_class=BinarySensorDeviceClass.RUNNING,
    icon="mdi:cog-play-outline",
    data_path="service",
    is_collection=True,
    collection_key="name",
    name_key="title",
    value_fn=lambda data: bool(data.get("running", False)),
    extra_attrs_fn=lambda data: {
        "name": data.get("name"),
        "enabled": data.get("enabled"),
    },
)

VM_RUNNING_BINARY_SENSOR = OMVBinarySensorDescription(
    key="vm_running",
    translation_key="vm_running",
    device_class=BinarySensorDeviceClass.RUNNING,
    icon="mdi:server",
    data_path="kvm",
    is_collection=True,
    collection_key="vm_key",
    name_key="name",
    value_fn=lambda data: bool(data.get("running", False)),
    extra_attrs_fn=lambda data: {
        "state": data.get("state"),
        "autostart": data.get("autostart"),
    },
)

UPS_ON_BATTERY_BINARY_SENSOR = OMVBinarySensorDescription(
    key="ups_on_battery",
    translation_key="ups_on_battery",
    icon="mdi:power-plug-off",
    data_path="nut",
    value_fn=lambda data: bool(data.get("on_battery")),
    extra_attrs_fn=lambda data: {
        "status": data.get("status"),
        "model": data.get("model"),
    },
)

RSYNC_JOB_ENABLED_BINARY_SENSOR = OMVBinarySensorDescription(
    key="rsync_job_enabled",
    translation_key="rsync_job_enabled",
    icon="mdi:sync",
    entity_category=EntityCategory.DIAGNOSTIC,
    data_path="rsync",
    is_collection=True,
    collection_key="rsync_key",
    name_key="name",
    value_fn=lambda data: bool(data.get("enabled", False)),
    extra_attrs_fn=lambda data: {
        "type": data.get("type"),
        "mode": data.get("mode"),
        "srcname": data.get("srcname"),
        "destname": data.get("destname"),
        "schedule": data.get("schedule"),
        "uuid": data.get("uuid"),
    },
)

DISK_BAD_SECTORS_BINARY_SENSOR = OMVBinarySensorDescription(
    key="disk_bad_sectors",
    translation_key="disk_bad_sectors",
    device_class=BinarySensorDeviceClass.PROBLEM,
    icon="mdi:harddisk-alert",
    entity_category=EntityCategory.DIAGNOSTIC,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    value_fn=lambda data: _smart_attribute_problem(data, "Reallocated_Sector_Ct"),
    extra_attrs_fn=lambda data: {"reallocated_sector_ct": data.get("Reallocated_Sector_Ct")},
)

DISK_CRC_ERRORS_BINARY_SENSOR = OMVBinarySensorDescription(
    key="disk_crc_errors",
    translation_key="disk_crc_errors",
    device_class=BinarySensorDeviceClass.PROBLEM,
    icon="mdi:harddisk-alert",
    entity_category=EntityCategory.DIAGNOSTIC,
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    value_fn=lambda data: _smart_attribute_problem(data, "UDMA_CRC_Error_Count"),
    extra_attrs_fn=lambda data: {"udma_crc_error_count": data.get("UDMA_CRC_Error_Count")},
)

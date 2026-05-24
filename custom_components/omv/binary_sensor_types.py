"""Binary sensor descriptions for the OpenMediaVault integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)


@dataclass(frozen=True, kw_only=True)
class OMVBinarySensorDescription(BinarySensorEntityDescription):
    """Describe an OMV binary sensor."""

    data_path: str
    value_fn: Callable[[dict[str, Any]], bool]
    extra_attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    is_collection: bool = False
    collection_key: str | None = None
    name_key: str | None = None


SYSTEM_BINARY_SENSORS: tuple[OMVBinarySensorDescription, ...] = (
    OMVBinarySensorDescription(
        key="reboot_required",
        translation_key="reboot_required",
        icon="mdi:restart-alert",
        data_path="hwinfo",
        value_fn=lambda data: bool(data.get("rebootRequired", False)),
    ),
)

DISK_SMART_PROBLEM_BINARY_SENSOR = OMVBinarySensorDescription(
    key="disk_smart_problem",
    translation_key="disk_smart_problem",
    device_class=BinarySensorDeviceClass.PROBLEM,
    icon="mdi:harddisk-remove",
    data_path="disk",
    is_collection=True,
    collection_key="disk_key",
    name_key="devicename",
    value_fn=lambda data: str(data.get("overallstatus") or "unknown") not in ("GOOD", "unknown"),
    extra_attrs_fn=lambda data: {"overall_status": data.get("overallstatus")},
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

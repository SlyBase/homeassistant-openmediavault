"""Binary sensor descriptions for the OpenMediaVault integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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
        key="update_available",
        translation_key="update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        icon="mdi:package-up",
        data_path="hwinfo",
        value_fn=lambda data: bool(data.get("pkgUpdatesAvailable", False)),
    ),
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
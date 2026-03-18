"""Sensor platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OMVDataUpdateCoordinator
from .entity import (
    OMVEntity,
    build_host_object_id,
    get_compose_project_device_info,
    get_container_device_info,
    get_disk_device_info,
    get_filesystem_device_info,
    get_storage_device_info,
)
from .sensor_types import (
    COMPOSE_PROJECT_SENSORS,
    COMPOSE_SENSORS,
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
    GPU_SENSORS,
    NETWORK_RX_SENSOR,
    NETWORK_TX_SENSOR,
    RAID_SENSOR,
    SYSTEM_SENSORS,
    ZFS_POOL_SENSOR,
    OMVSensorDescription,
)

_DISK_SENSORS: tuple[OMVSensorDescription, ...] = (
    DISK_USED_PERCENT_SENSOR,
    DISK_FREE_PERCENT_SENSOR,
    DISK_USED_SIZE_SENSOR,
    DISK_FREE_SIZE_SENSOR,
    DISK_TOTAL_SIZE_SENSOR,
)

_FILESYSTEM_SENSORS: tuple[OMVSensorDescription, ...] = (
    FILESYSTEM_SENSOR,
    FILESYSTEM_FREE_PERCENT_SENSOR,
    FILESYSTEM_USED_SIZE_SENSOR,
    FILESYSTEM_FREE_SIZE_SENSOR,
    FILESYSTEM_TOTAL_SIZE_SENSOR,
)

_COLLECTION_SENSORS: tuple[OMVSensorDescription, ...] = (
    NETWORK_TX_SENSOR,
    NETWORK_RX_SENSOR,
    RAID_SENSOR,
    ZFS_POOL_SENSOR,
)


def _sensor_metric_slug(description: OMVSensorDescription) -> str:
    """Return the metric portion for a suggested object ID."""
    if description.key == "disk":
        return "temperature"
    if description.key == "filesystem":
        return "used_percent"
    if description.key == "raid":
        return "health"
    for prefix in (
        "disk_",
        "filesystem_",
        "compose_project_",
        "container_",
        "docker_",
    ):
        if description.key.startswith(prefix):
            return description.key.removeprefix(prefix)
    return description.key


def _sensor_suggested_object_id(
    coordinator: OMVDataUpdateCoordinator,
    description: OMVSensorDescription,
    item_key: str | None,
    data: dict[str, Any],
) -> str:
    """Build one host-qualified suggested object ID for a sensor."""
    metric = _sensor_metric_slug(description)
    if not item_key:
        return build_host_object_id(coordinator, metric)

    if description.data_path == "disk":
        return build_host_object_id(coordinator, "disk", item_key, metric)
    if description.data_path == "fs":
        return build_host_object_id(coordinator, "filesystem", item_key, metric)
    if description.data_path == "compose_projects":
        return build_host_object_id(
            coordinator,
            "compose",
            data.get("name") or item_key,
            metric,
        )
    if description.data_path == "compose":
        return build_host_object_id(
            coordinator,
            "container",
            data.get("name") or item_key,
            metric,
        )
    if description.data_path == "compose_volumes":
        return build_host_object_id(
            coordinator,
            "container",
            data.get("container_name") or data.get("container_key") or item_key,
            "volume",
            data.get("display_name") or data.get("name") or item_key,
            metric,
        )
    return build_host_object_id(coordinator, description.data_path, item_key, metric)


def _collect_device_identifiers(device_info, expected_identifiers: set[tuple[str, str]]) -> None:
    """Collect device registry identifiers from a DeviceInfo mapping."""
    if not device_info:
        return
    identifiers = device_info.get("identifiers")
    if not isinstance(identifiers, set):
        return
    for identifier in identifiers:
        if (
            isinstance(identifier, tuple)
            and len(identifier) == 2
            and isinstance(identifier[0], str)
            and isinstance(identifier[1], str)
        ):
            expected_identifiers.add(identifier)


def _should_add_description(description: OMVSensorDescription, data: dict[str, Any]) -> bool:
    """Return whether a sensor description has a concrete value for an item."""
    if description.key == "container_volume_size":
        return bool(data.get("volume_key"))
    return description.value_fn(data) is not None


def get_expected_sensor_registry_state(
    coordinator: OMVDataUpdateCoordinator,
) -> tuple[set[str], set[tuple[str, str]]]:
    """Return expected sensor unique IDs and child device identifiers for runtime data."""
    entry_id = coordinator.config_entry.entry_id
    unique_ids: set[str] = set()
    device_identifiers: set[tuple[str, str]] = set()

    hwinfo = coordinator.data.get("hwinfo", {})
    if isinstance(hwinfo, dict):
        for description in SYSTEM_SENSORS:
            if coordinator.virtual_passthrough and description.key == "cpu_temperature":
                continue
            if _should_add_description(description, hwinfo):
                unique_ids.add(f"{entry_id}-{description.key}")

    gpu_data = coordinator.data.get("gpu", {})
    if isinstance(gpu_data, dict):
        for description in GPU_SENSORS:
            if _should_add_description(description, gpu_data):
                unique_ids.add(f"{entry_id}-{description.key}")

    compose_summary = coordinator.data.get("compose_summary", {})
    if isinstance(compose_summary, dict):
        for description in COMPOSE_SENSORS:
            if _should_add_description(description, compose_summary):
                unique_ids.add(f"{entry_id}-{description.key}")

    for disk in coordinator.data.get("disk", []):
        if not isinstance(disk, dict):
            continue
        item_key = str(disk.get("disk_key") or disk.get("devicename") or "")
        if not item_key:
            continue
        if not coordinator.virtual_passthrough and disk.get("temperature") is not None:
            unique_ids.add(f"{entry_id}-{DISK_SENSOR.key}-{item_key}")
            _collect_device_identifiers(
                get_disk_device_info(coordinator, disk),
                device_identifiers,
            )
        for description in _DISK_SENSORS:
            if _should_add_description(description, disk):
                unique_ids.add(f"{entry_id}-{description.key}-{item_key}")
                _collect_device_identifiers(
                    get_disk_device_info(coordinator, disk),
                    device_identifiers,
                )

    for description in _FILESYSTEM_SENSORS:
        for filesystem in coordinator.data.get("fs", []):
            if not isinstance(filesystem, dict):
                continue
            item_key = str(filesystem.get("uuid") or "")
            if not item_key or not _should_add_description(description, filesystem):
                continue
            unique_ids.add(f"{entry_id}-{description.key}-{item_key}")
            _collect_device_identifiers(
                get_filesystem_device_info(coordinator, filesystem),
                device_identifiers,
            )

    for description in COMPOSE_PROJECT_SENSORS:
        for project in coordinator.data.get("compose_projects", []):
            if not isinstance(project, dict):
                continue
            item_key = str(project.get("project_key") or project.get("name") or "")
            if not item_key or not _should_add_description(description, project):
                continue
            unique_ids.add(f"{entry_id}-{description.key}-{item_key}")
            _collect_device_identifiers(
                get_compose_project_device_info(coordinator, project),
                device_identifiers,
            )

    for description in CONTAINER_SENSORS:
        for container in coordinator.data.get("compose", []):
            if not isinstance(container, dict):
                continue
            item_key = str(container.get("container_key") or container.get("name") or "")
            if not item_key or not _should_add_description(description, container):
                continue
            unique_ids.add(f"{entry_id}-{description.key}-{item_key}")
            _collect_device_identifiers(
                get_container_device_info(coordinator, container),
                device_identifiers,
            )

    for description in CONTAINER_VOLUME_SENSORS:
        for volume in coordinator.data.get("compose_volumes", []):
            if not isinstance(volume, dict):
                continue
            item_key = str(volume.get("volume_key") or "")
            if not item_key or not _should_add_description(description, volume):
                continue
            unique_ids.add(f"{entry_id}-{description.key}-{item_key}")
            _collect_device_identifiers(
                get_container_device_info(coordinator, volume),
                device_identifiers,
            )

    for description in _COLLECTION_SENSORS:
        for item in coordinator.data.get(description.data_path, []):
            if not isinstance(item, dict):
                continue
            item_key = str(item.get(description.collection_key or "") or "")
            if not item_key or not _should_add_description(description, item):
                continue
            unique_ids.add(f"{entry_id}-{description.key}-{item_key}")
            _collect_device_identifiers(
                get_storage_device_info(coordinator, item),
                device_identifiers,
            )

    return unique_ids, device_identifiers


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OMV sensors from a config entry."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data

    entities: list[OMVSensor] = [
        OMVSensor(coordinator, description)
        for description in SYSTEM_SENSORS
        if not (coordinator.virtual_passthrough and description.key == "cpu_temperature")
        and _should_add_description(description, coordinator.data.get("hwinfo", {}))
    ]

    gpu_data = coordinator.data.get("gpu", {})
    if isinstance(gpu_data, dict):
        entities.extend(
            OMVSensor(coordinator, description)
            for description in GPU_SENSORS
            if _should_add_description(description, gpu_data)
        )

    compose_summary = coordinator.data.get("compose_summary", {})
    if isinstance(compose_summary, dict):
        entities.extend(
            OMVSensor(coordinator, description)
            for description in COMPOSE_SENSORS
            if _should_add_description(description, compose_summary)
        )

    for description in COMPOSE_PROJECT_SENSORS:
        for project in coordinator.data.get("compose_projects", []):
            if not isinstance(project, dict):
                continue
            item_key = str(project.get("project_key") or project.get("name") or "")
            if not item_key or not _should_add_description(description, project):
                continue
            entities.append(
                OMVSensor(
                    coordinator,
                    description,
                    item_key=item_key,
                    device_info=get_compose_project_device_info(coordinator, project),
                )
            )

    for description in CONTAINER_SENSORS:
        for container in coordinator.data.get("compose", []):
            if not isinstance(container, dict):
                continue
            item_key = str(container.get("container_key") or container.get("name") or "")
            if not item_key or not _should_add_description(description, container):
                continue
            entities.append(
                OMVSensor(
                    coordinator,
                    description,
                    item_key=item_key,
                    device_info=get_container_device_info(coordinator, container),
                )
            )

    for description in CONTAINER_VOLUME_SENSORS:
        for volume in coordinator.data.get("compose_volumes", []):
            if not isinstance(volume, dict):
                continue
            item_key = str(volume.get("volume_key") or "")
            if not item_key or not _should_add_description(description, volume):
                continue
            entities.append(
                OMVSensor(
                    coordinator,
                    description,
                    item_key=item_key,
                    device_info=get_container_device_info(coordinator, volume),
                )
            )

    for disk in coordinator.data.get("disk", []):
        if not isinstance(disk, dict):
            continue
        item_key = str(disk.get("disk_key") or disk.get("devicename") or "")
        if not item_key:
            continue
        device_info = get_disk_device_info(coordinator, disk)
        if not coordinator.virtual_passthrough and disk.get("temperature") is not None:
            entities.append(
                OMVSensor(
                    coordinator,
                    DISK_SENSOR,
                    item_key=item_key,
                    device_info=device_info,
                )
            )
        for description in _DISK_SENSORS:
            if not _should_add_description(description, disk):
                continue
            entities.append(
                OMVSensor(
                    coordinator,
                    description,
                    item_key=item_key,
                    device_info=device_info,
                )
            )

    for description in _FILESYSTEM_SENSORS:
        for filesystem in coordinator.data.get("fs", []):
            if not isinstance(filesystem, dict):
                continue
            item_key = str(filesystem.get("uuid") or "")
            if not item_key or not _should_add_description(description, filesystem):
                continue
            entities.append(
                OMVSensor(
                    coordinator,
                    description,
                    item_key=item_key,
                    device_info=get_filesystem_device_info(coordinator, filesystem),
                )
            )

    for description in _COLLECTION_SENSORS:
        items = coordinator.data.get(description.data_path, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get(description.collection_key or "") or "")
            if not item_key or not _should_add_description(description, item):
                continue
            entities.append(
                OMVSensor(
                    coordinator,
                    description,
                    item_key=item_key,
                    device_info=get_storage_device_info(coordinator, item),
                )
            )

    async_add_entities(entities)


class OMVSensor(OMVEntity, SensorEntity):
    """Represent an OMV sensor entity."""

    entity_description: OMVSensorDescription

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        description: OMVSensorDescription,
        item_key: str | None = None,
        device_info=None,
    ) -> None:
        """Initialize the sensor."""
        uid = f"{description.key}-{item_key}" if item_key else description.key
        if device_info is None and item_key and description.is_collection:
            item = next(
                (
                    item
                    for item in coordinator.data.get(description.data_path, [])
                    if isinstance(item, dict) and str(item.get(description.collection_key or "") or "") == item_key
                ),
                None,
            )
            if item is not None:
                if description.data_path == "disk":
                    device_info = get_disk_device_info(coordinator, item)
                elif description.data_path == "fs":
                    device_info = get_filesystem_device_info(coordinator, item)
                elif description.data_path == "compose_projects":
                    device_info = get_compose_project_device_info(coordinator, item)
                elif description.data_path in {"compose", "compose_volumes"}:
                    device_info = get_container_device_info(coordinator, item)
                else:
                    device_info = get_storage_device_info(coordinator, item)
        super().__init__(coordinator, uid, device_info=device_info)
        self.entity_description = description
        self._item_key = item_key

        if item_key:
            data = self._get_data()
            display_name = str(data.get(description.name_key or "") or item_key)
            if description.translation_key:
                self._attr_translation_placeholders = {"resource": display_name}
            elif description.name:
                self._attr_name = f"{display_name} {description.name}"
            else:
                self._attr_name = display_name
            self._attr_suggested_object_id = _sensor_suggested_object_id(
                coordinator,
                description,
                item_key,
                data,
            )
        else:
            self._attr_suggested_object_id = _sensor_suggested_object_id(
                coordinator,
                description,
                None,
                self._get_data(),
            )

    def _get_data(self) -> dict[str, Any]:
        """Return the current data object for this entity."""
        raw = self.coordinator.data.get(self.entity_description.data_path, {})
        if self.entity_description.is_collection and isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if item.get(self.entity_description.collection_key or "") == self._item_key:
                    return item
            return {}
        return raw if isinstance(raw, dict) else {}

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self._get_data())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return sensor attributes."""
        if not self.entity_description.extra_attrs_fn:
            return None
        attributes = self.entity_description.extra_attrs_fn(self._get_data())
        return {key: value for key, value in attributes.items() if value not in (None, "")}

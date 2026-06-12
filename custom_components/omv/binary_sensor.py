"""Binary sensor platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor_types import (
    DISK_BAD_SECTORS_BINARY_SENSOR,
    DISK_CRC_ERRORS_BINARY_SENSOR,
    RSYNC_JOB_ENABLED_BINARY_SENSOR,
    SERVICE_BINARY_SENSOR,
    SYSTEM_BINARY_SENSORS,
    UPS_ON_BATTERY_BINARY_SENSOR,
    VM_RUNNING_BINARY_SENSOR,
    ZFS_POOL_SCRUB_ACTIVE_BINARY_SENSOR,
    OMVBinarySensorDescription,
)
from .coordinator import OMVDataUpdateCoordinator
from .entity import (
    OMVEntity,
    build_host_object_id,
    disk_is_smart_eligible,
    get_disk_device_info,
    get_storage_device_info,
    get_vm_device_info,
)

_DISK_BINARY_SENSORS: tuple[OMVBinarySensorDescription, ...] = (
    DISK_BAD_SECTORS_BINARY_SENSOR,
    DISK_CRC_ERRORS_BINARY_SENSOR,
)


def _binary_sensor_suggested_object_id(
    coordinator: OMVDataUpdateCoordinator,
    description: OMVBinarySensorDescription,
    item_key: str | None,
    data: dict[str, Any],
) -> str:
    """Build one host-qualified suggested object ID for a binary sensor."""
    if not item_key:
        return build_host_object_id(coordinator, description.key)
    if description.data_path == "kvm":
        metric = description.key.removeprefix("vm_")
        return build_host_object_id(coordinator, "vm", data.get("name") or item_key, metric)
    if description.data_path == "rsync":
        return build_host_object_id(coordinator, "rsync", data.get("name") or item_key, "enabled")
    if description.data_path == "zfs":
        return build_host_object_id(coordinator, "zfs", data.get("name") or item_key, "scrub_active")
    return build_host_object_id(coordinator, description.key, item_key)


def get_expected_binary_sensor_unique_ids(
    coordinator: OMVDataUpdateCoordinator,
) -> set[str]:
    """Return the binary sensor unique IDs for the current runtime data."""
    entry_id = coordinator.config_entry.entry_id
    unique_ids = {f"{entry_id}-{description.key}" for description in SYSTEM_BINARY_SENSORS}

    for service in coordinator.data.get("service", []):
        if not isinstance(service, dict):
            continue
        service_name = str(service.get("name") or "")
        if service_name:
            unique_ids.add(f"{entry_id}-{SERVICE_BINARY_SENSOR.key}-{service_name}")

    for disk in coordinator.data.get("disk", []):
        if not isinstance(disk, dict):
            continue
        item_key = str(disk.get("disk_key") or disk.get("devicename") or "")
        if item_key and disk_is_smart_eligible(disk):
            for description in _DISK_BINARY_SENSORS:
                unique_ids.add(f"{entry_id}-{description.key}-{item_key}")

    for vm in coordinator.data.get("kvm", []):
        if not isinstance(vm, dict):
            continue
        item_key = str(vm.get("vm_key") or "")
        if item_key:
            unique_ids.add(f"{entry_id}-{VM_RUNNING_BINARY_SENSOR.key}-{item_key}")

    nut_data = coordinator.data.get("nut", {})
    if isinstance(nut_data, dict) and nut_data:
        unique_ids.add(f"{entry_id}-{UPS_ON_BATTERY_BINARY_SENSOR.key}")

    for job in coordinator.data.get("rsync", []):
        if not isinstance(job, dict):
            continue
        item_key = str(job.get("rsync_key") or "")
        if item_key:
            unique_ids.add(f"{entry_id}-{RSYNC_JOB_ENABLED_BINARY_SENSOR.key}-{item_key}")

    for pool in coordinator.data.get("zfs", []):
        if not isinstance(pool, dict):
            continue
        item_key = str(pool.get("name") or "")
        if item_key:
            unique_ids.add(f"{entry_id}-{ZFS_POOL_SCRUB_ACTIVE_BINARY_SENSOR.key}-{item_key}")

    return unique_ids


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OMV binary sensors."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data
    entities: list[OMVBinarySensor] = [
        OMVBinarySensor(coordinator, description) for description in SYSTEM_BINARY_SENSORS
    ]

    for service in coordinator.data.get("service", []):
        if not isinstance(service, dict):
            continue
        name = str(service.get("name") or "")
        if not name:
            continue
        entities.append(OMVBinarySensor(coordinator, SERVICE_BINARY_SENSOR, item_key=name))

    for disk in coordinator.data.get("disk", []):
        if not isinstance(disk, dict):
            continue
        item_key = str(disk.get("disk_key") or disk.get("devicename") or "")
        if not item_key or not disk_is_smart_eligible(disk):
            continue
        device_info = get_disk_device_info(coordinator, disk)
        for description in _DISK_BINARY_SENSORS:
            entities.append(OMVBinarySensor(coordinator, description, item_key=item_key, device_info=device_info))

    for vm in coordinator.data.get("kvm", []):
        if not isinstance(vm, dict):
            continue
        item_key = str(vm.get("vm_key") or "")
        if not item_key:
            continue
        entities.append(
            OMVBinarySensor(
                coordinator,
                VM_RUNNING_BINARY_SENSOR,
                item_key=item_key,
                device_info=get_vm_device_info(coordinator, vm),
            )
        )

    nut_data = coordinator.data.get("nut", {})
    if isinstance(nut_data, dict) and nut_data:
        entities.append(OMVBinarySensor(coordinator, UPS_ON_BATTERY_BINARY_SENSOR))

    for job in coordinator.data.get("rsync", []):
        if not isinstance(job, dict):
            continue
        item_key = str(job.get("rsync_key") or "")
        if not item_key:
            continue
        entities.append(OMVBinarySensor(coordinator, RSYNC_JOB_ENABLED_BINARY_SENSOR, item_key=item_key))

    for pool in coordinator.data.get("zfs", []):
        if not isinstance(pool, dict):
            continue
        item_key = str(pool.get("name") or "")
        if not item_key:
            continue
        entities.append(
            OMVBinarySensor(
                coordinator,
                ZFS_POOL_SCRUB_ACTIVE_BINARY_SENSOR,
                item_key=item_key,
                device_info=get_storage_device_info(coordinator, pool),
            )
        )

    async_add_entities(entities)


class OMVBinarySensor(OMVEntity, BinarySensorEntity):
    """Represent an OMV binary sensor."""

    entity_description: OMVBinarySensorDescription

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        description: OMVBinarySensorDescription,
        item_key: str | None = None,
        device_info: DeviceInfo | None = None,
    ) -> None:
        uid = f"{description.key}-{item_key}" if item_key else description.key
        if device_info is None and item_key and description.data_path == "disk":
            disk = next(
                (
                    item
                    for item in coordinator.data.get("disk", [])
                    if isinstance(item, dict) and str(item.get(description.collection_key or "") or "") == item_key
                ),
                None,
            )
            if disk is not None:
                device_info = get_disk_device_info(coordinator, disk)
        elif device_info is None and item_key and description.data_path == "kvm":
            vm = next(
                (
                    item
                    for item in coordinator.data.get("kvm", [])
                    if isinstance(item, dict) and str(item.get(description.collection_key or "") or "") == item_key
                ),
                None,
            )
            if vm is not None:
                device_info = get_vm_device_info(coordinator, vm)
        elif device_info is None and item_key and description.data_path == "zfs":
            pool = next(
                (
                    item
                    for item in coordinator.data.get("zfs", [])
                    if isinstance(item, dict) and str(item.get(description.collection_key or "") or "") == item_key
                ),
                None,
            )
            if pool is not None:
                device_info = get_storage_device_info(coordinator, pool)
        super().__init__(coordinator, uid, device_info=device_info)
        self.entity_description = description
        self._item_key = item_key
        data = self._get_data() if item_key else {}
        self._attr_suggested_object_id = _binary_sensor_suggested_object_id(
            coordinator,
            description,
            item_key,
            data,
        )

        if item_key:
            display_name = str(data.get(description.name_key or "") or item_key)
            if description.translation_key:
                self._attr_translation_placeholders = {"resource": display_name}
            else:
                self._attr_name = display_name

    def _container_stats(self) -> dict[str, int]:
        """Return aggregate container counts for Docker/Compose services."""
        containers = self.coordinator.data.get("compose", [])
        if not isinstance(containers, list):
            return {}

        total = 0
        running = 0
        for container in containers:
            if not isinstance(container, dict):
                continue
            total += 1
            if self._is_container_running(container):
                running += 1

        return {
            "container_total": total,
            "container_running": running,
            "container_not_running": max(0, total - running),
        }

    def _is_container_running(self, container: dict[str, Any]) -> bool:
        """Return whether a compose container is currently running."""
        if container.get("running") is True:
            return True

        state = str(container.get("state") or "").strip().lower()
        if state in {"running", "healthy"}:
            return True
        if state.startswith("running "):
            return True

        status = str(container.get("status_detail") or container.get("status") or "").strip().lower()
        if status in {"running", "up", "healthy"}:
            return True
        return status.startswith("up ") or status.startswith("running ")

    def _is_container_service(self, data: dict[str, Any]) -> bool:
        """Return whether the sensor represents Docker/Compose."""
        name = str(data.get("name") or "").strip().lower()
        title = str(data.get("title") or "").strip().lower()
        return name in {"compose", "docker"} or "docker" in title or "compose" in title

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
    def is_on(self) -> bool | None:
        """Return whether the binary sensor is on, or None if unknown."""
        return self.entity_description.value_fn(self._get_data())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return binary sensor attributes."""
        if not self.entity_description.extra_attrs_fn:
            return None
        data = self._get_data()
        attributes = self.entity_description.extra_attrs_fn(data)
        if self.entity_description is SERVICE_BINARY_SENSOR and self._is_container_service(data):
            attributes = {**attributes, **self._container_stats()}
        return {key: value for key, value in attributes.items() if value not in (None, "")}

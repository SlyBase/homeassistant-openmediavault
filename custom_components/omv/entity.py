"""Shared entity classes for the OpenMediaVault integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OMVDataUpdateCoordinator


def _normalized_device_value(value: Any) -> str | None:
    """Return a cleaned device metadata value or None for placeholders."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or text.lower() == "unknown":
        return None
    return text


def _slugify_object_id_part(value: Any) -> str:
    """Return a Home-Assistant-friendly slug for object IDs."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "omv"


def get_hostname_slug(coordinator: OMVDataUpdateCoordinator) -> str:
    """Return a stable hostname slug for object IDs."""
    hwinfo = coordinator.data.get("hwinfo", {})
    if isinstance(hwinfo, dict):
        hostname = str(hwinfo.get("hostname") or "").strip()
        if hostname:
            return _slugify_object_id_part(hostname)

    if coordinator.config_entry.unique_id:
        return _slugify_object_id_part(coordinator.config_entry.unique_id)

    return _slugify_object_id_part(coordinator.config_entry.title or DOMAIN)


def build_host_object_id(
    coordinator: OMVDataUpdateCoordinator,
    *parts: Any,
) -> str:
    """Build a host-qualified suggested object ID."""
    normalized_parts = [_slugify_object_id_part(get_hostname_slug(coordinator))]
    normalized_parts.extend(
        _slugify_object_id_part(part) for part in parts if str(part or "").strip()
    )
    return "_".join(part for part in normalized_parts if part)


def _build_disk_device_name(disk: dict[str, Any], disk_key: str) -> str:
    """Build a readable Home Assistant device name for physical and logical disks."""
    storage_label = _normalized_device_value(disk.get("storage_label"))
    vendor = _normalized_device_value(disk.get("vendor"))
    model = _normalized_device_value(disk.get("model"))
    if storage_label and _is_generic_storage_label(disk_key, storage_label):
        storage_label = None

    if disk.get("israid") or disk.get("is_logical"):
        name = f"RAID {disk_key}"
        if storage_label:
            name = f"{name} ({storage_label})"
        return name

    readable_parts = _deduplicated_vendor_model(vendor, model)
    if readable_parts:
        name = " ".join(readable_parts)
        if storage_label:
            name = f"{name} ({storage_label})"
        return f"{name} [{disk_key}]"

    return f"Disk {disk_key}"


def _deduplicated_vendor_model(vendor: str | None, model: str | None) -> list[str]:
    """Return vendor and model without duplicated vendor prefixes."""
    if vendor and model and model.casefold().startswith(vendor.casefold()):
        return [model]
    return [part for part in (vendor, model) if part]


def _is_generic_storage_label(disk_key: str, label: str) -> bool:
    """Return whether a storage label is only a device or partition reference."""
    normalized = label.strip()
    if normalized.startswith("/dev/"):
        normalized = normalized[5:]
    if normalized.startswith("mapper/"):
        normalized = normalized[len("mapper/") :]
    if normalized == disk_key:
        return True
    return bool(
        re.fullmatch(rf"{re.escape(disk_key)}(?:p\d+|\d+)", normalized)
    )


def _build_disk_device_model(disk: dict[str, Any]) -> str | None:
    """Return a concise device model string."""
    raid_level = _normalized_device_value(disk.get("raid_level"))
    if disk.get("israid") or disk.get("is_logical"):
        source = _normalized_device_value(disk.get("storage_source"))
        if source == "zfs":
            base_model = "Linux MD RAID backed by ZFS"
        else:
            base_model = _normalized_device_value(disk.get("model")) or "Linux MD RAID"
        if raid_level and raid_level != "unknown":
            return f"{base_model} ({raid_level})"
        return base_model
    return _normalized_device_value(disk.get("model"))


def _get_disk_by_key(
    coordinator: OMVDataUpdateCoordinator,
    disk_key: str,
) -> dict[str, Any] | None:
    """Return the normalized disk record for a disk key."""
    for source in (
        coordinator.data.get("disk", []),
        coordinator._inventory_source.get("disk", []),
    ):
        match = next(
            (
                item
                for item in source
                if isinstance(item, dict) and str(item.get("disk_key") or "") == disk_key
            ),
            None,
        )
        if match is not None:
            return match
    return None


def get_hub_device_info(coordinator: OMVDataUpdateCoordinator) -> DeviceInfo:
    """Return the OMV hub device info."""
    hwinfo = coordinator.data.get("hwinfo", {})
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"OMV ({hwinfo.get('hostname', 'Unknown')})",
        manufacturer="OpenMediaVault",
        model=hwinfo.get("cpuModel") or "OpenMediaVault",
        hw_version=hwinfo.get("kernel") or None,
        sw_version=hwinfo.get("version") or None,
        configuration_url=coordinator.api.base_url,
    )


def get_disk_device_identifier(
    coordinator: OMVDataUpdateCoordinator, disk_key: str
) -> tuple[str, str]:
    """Return the stable device registry identifier tuple for a disk."""
    return (DOMAIN, f"{coordinator.config_entry.entry_id}:disk:{disk_key}")


def get_compose_project_device_identifier(
    coordinator: OMVDataUpdateCoordinator, project_key: str
) -> tuple[str, str]:
    """Return the stable device registry identifier tuple for a compose project."""
    return (DOMAIN, f"{coordinator.config_entry.entry_id}:compose_project:{project_key}")


def get_container_device_identifier(
    coordinator: OMVDataUpdateCoordinator, container_key: str
) -> tuple[str, str]:
    """Return the stable device registry identifier tuple for a container."""
    return (DOMAIN, f"{coordinator.config_entry.entry_id}:container:{container_key}")


def get_disk_device_info(
    coordinator: OMVDataUpdateCoordinator,
    disk: dict[str, Any],
) -> DeviceInfo:
    """Return device info for a disk-attached entity."""
    disk_key = str(disk.get("disk_key") or disk.get("devicename") or "")
    manufacturer = _normalized_device_value(disk.get("vendor"))
    if disk.get("israid") or disk.get("is_logical"):
        manufacturer = manufacturer or "OpenMediaVault"

    return DeviceInfo(
        identifiers={get_disk_device_identifier(coordinator, disk_key)},
        via_device=(DOMAIN, coordinator.config_entry.entry_id),
        name=_build_disk_device_name(disk, disk_key),
        manufacturer=manufacturer,
        model=_build_disk_device_model(disk),
        hw_version=_normalized_device_value(disk.get("serialnumber")) or disk_key,
        configuration_url=coordinator.api.base_url,
    )


def get_filesystem_device_info(
    coordinator: OMVDataUpdateCoordinator,
    filesystem: dict[str, Any],
) -> DeviceInfo:
    """Return device info for a filesystem entity."""
    disk_key = str(filesystem.get("disk_key") or "")
    if disk_key and (disk := _get_disk_by_key(coordinator, disk_key)) is not None:
        return get_disk_device_info(coordinator, disk)
    return get_hub_device_info(coordinator)


def get_storage_device_info(
    coordinator: OMVDataUpdateCoordinator,
    item: dict[str, Any],
) -> DeviceInfo:
    """Return the most specific storage device info for an item."""
    disk_key = str(item.get("disk_key") or "")
    if disk_key and (disk := _get_disk_by_key(coordinator, disk_key)) is not None:
        return get_disk_device_info(coordinator, disk)
    return get_hub_device_info(coordinator)


def get_compose_project_device_info(
    coordinator: OMVDataUpdateCoordinator,
    project: dict[str, Any],
) -> DeviceInfo:
    """Return device info for a compose project entity."""
    project_key = str(project.get("project_key") or project.get("name") or "")
    project_name = _normalized_device_value(project.get("name")) or project_key
    return DeviceInfo(
        identifiers={get_compose_project_device_identifier(coordinator, project_key)},
        via_device=(DOMAIN, coordinator.config_entry.entry_id),
        name=f"Compose {project_name}",
        manufacturer="Docker Compose",
        model="Compose Project",
        configuration_url=coordinator.api.base_url,
    )


def _container_display_name(data: dict[str, Any]) -> str:
    """Return the most useful display name for a container-backed device."""
    return str(
        data.get("container_name")
        or data.get("name")
        or data.get("container_key")
        or ""
    ).strip()


def get_container_device_info(
    coordinator: OMVDataUpdateCoordinator,
    container: dict[str, Any],
) -> DeviceInfo:
    """Return device info for a Docker container entity."""
    container_key = str(container.get("container_key") or container.get("name") or "")
    project_key = str(container.get("project_key") or "")
    via_device = (
        get_compose_project_device_identifier(coordinator, project_key)
        if project_key
        else (DOMAIN, coordinator.config_entry.entry_id)
    )
    display_name = _container_display_name(container)
    return DeviceInfo(
        identifiers={get_container_device_identifier(coordinator, container_key)},
        via_device=via_device,
        name=f"Container {display_name or container_key}",
        manufacturer="Docker",
        model=_normalized_device_value(container.get("image")) or "Docker Container",
        sw_version=_normalized_device_value(container.get("version")),
        configuration_url=coordinator.api.base_url,
    )


class OMVEntity(CoordinatorEntity[OMVDataUpdateCoordinator]):
    """Base entity for the OMV integration."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        uid_suffix: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}-{uid_suffix}"
        self._attr_device_info = device_info or get_hub_device_info(coordinator)
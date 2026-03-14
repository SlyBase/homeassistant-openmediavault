"""Shared entity classes for the OpenMediaVault integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OMVDataUpdateCoordinator


class OMVEntity(CoordinatorEntity[OMVDataUpdateCoordinator]):
    """Base entity for the OMV integration."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OMVDataUpdateCoordinator, uid_suffix: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        hostname = coordinator.data.get("hwinfo", {}).get("hostname", "Unknown")
        self._attr_unique_id = f"{entry.entry_id}-{uid_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"OMV ({hostname})",
            manufacturer="OpenMediaVault",
            sw_version=coordinator.data.get("hwinfo", {}).get("version"),
            configuration_url=coordinator.api.base_url,
        )
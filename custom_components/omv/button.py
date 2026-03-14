"""Button platform for the OpenMediaVault integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OMV button entities."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data
    async_add_entities([
        OMVRebootButton(coordinator),
        OMVShutdownButton(coordinator),
    ])


class OMVRebootButton(OMVEntity, ButtonEntity):
    """Button to reboot the OMV host."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "reboot"

    def __init__(self, coordinator: OMVDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "reboot")

    async def async_press(self) -> None:
        """Trigger a reboot on the OMV host."""
        await self.coordinator.api.async_call("System", "reboot")


class OMVShutdownButton(OMVEntity, ButtonEntity):
    """Button to shut down the OMV host."""

    _attr_translation_key = "shutdown"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: OMVDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "shutdown")

    async def async_press(self) -> None:
        """Trigger a shutdown on the OMV host."""
        await self.coordinator.api.async_call("System", "shutdown")
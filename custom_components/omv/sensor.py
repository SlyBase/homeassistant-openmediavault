"""Sensor platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity
from .sensor_types import (
    DISK_SENSOR,
    FILESYSTEM_SENSOR,
    NETWORK_RX_SENSOR,
    NETWORK_TX_SENSOR,
    OMVSensorDescription,
    RAID_SENSOR,
    SYSTEM_SENSORS,
    ZFS_POOL_SENSOR,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OMV sensors from a config entry."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data
    entities: list[OMVSensor] = [OMVSensor(coordinator, description) for description in SYSTEM_SENSORS]

    for collection_description in (
        DISK_SENSOR,
        FILESYSTEM_SENSOR,
        NETWORK_TX_SENSOR,
        NETWORK_RX_SENSOR,
        RAID_SENSOR,
        ZFS_POOL_SENSOR,
    ):
        items = coordinator.data.get(collection_description.data_path, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get(collection_description.collection_key or "") or "")
            if not item_key:
                continue
            entities.append(OMVSensor(coordinator, collection_description, item_key=item_key))

    async_add_entities(entities)


class OMVSensor(OMVEntity, SensorEntity):
    """Represent an OMV sensor entity."""

    entity_description: OMVSensorDescription

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        description: OMVSensorDescription,
        item_key: str | None = None,
    ) -> None:
        uid = f"{description.key}-{item_key}" if item_key else description.key
        super().__init__(coordinator, uid)
        self.entity_description = description
        self._item_key = item_key

        if item_key:
            data = self._get_data()
            display_name = str(data.get(description.name_key or "") or item_key)
            if description.name:
                self._attr_name = f"{display_name} {description.name}"
            else:
                self._attr_name = display_name

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
"""Binary sensor platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor_types import (
    OMVBinarySensorDescription,
    SERVICE_BINARY_SENSOR,
    SYSTEM_BINARY_SENSORS,
)
from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity


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

    async_add_entities(entities)


class OMVBinarySensor(OMVEntity, BinarySensorEntity):
    """Represent an OMV binary sensor."""

    entity_description: OMVBinarySensorDescription

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        description: OMVBinarySensorDescription,
        item_key: str | None = None,
    ) -> None:
        uid = f"{description.key}-{item_key}" if item_key else description.key
        super().__init__(coordinator, uid)
        self.entity_description = description
        self._item_key = item_key

        if item_key:
            data = self._get_data()
            self._attr_name = str(data.get(description.name_key or "") or item_key)

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
    def is_on(self) -> bool:
        """Return whether the binary sensor is on."""
        return self.entity_description.value_fn(self._get_data())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return binary sensor attributes."""
        if not self.entity_description.extra_attrs_fn:
            return None
        attributes = self.entity_description.extra_attrs_fn(self._get_data())
        return {key: value for key, value in attributes.items() if value not in (None, "")}
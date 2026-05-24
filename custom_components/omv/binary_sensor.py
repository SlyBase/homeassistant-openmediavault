"""Binary sensor platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor_types import (
    SERVICE_BINARY_SENSOR,
    SYSTEM_BINARY_SENSORS,
    OMVBinarySensorDescription,
)
from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity, build_host_object_id


def _binary_sensor_suggested_object_id(
    coordinator: OMVDataUpdateCoordinator,
    description: OMVBinarySensorDescription,
    item_key: str | None,
) -> str:
    """Build one host-qualified suggested object ID for a binary sensor."""
    if item_key:
        return build_host_object_id(coordinator, description.key, item_key)
    return build_host_object_id(coordinator, description.key)


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
        self._attr_suggested_object_id = _binary_sensor_suggested_object_id(
            coordinator,
            description,
            item_key,
        )

        if item_key:
            data = self._get_data()
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
    def is_on(self) -> bool:
        """Return whether the binary sensor is on."""
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

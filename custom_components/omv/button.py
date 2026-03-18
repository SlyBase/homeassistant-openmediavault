"""Button platform for the OpenMediaVault integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity, build_host_object_id, get_compose_project_device_info

_COMPOSE_PROJECT_ACTIONS: tuple[tuple[int, str, str, str], ...] = (
    (1, "compose_up", "up -d", "mdi:arrow-up-bold-box-outline"),
    (2, "compose_down", "down", "mdi:arrow-down-bold-box-outline"),
    (3, "compose_start", "start", "mdi:play-circle-outline"),
    (4, "compose_stop", "stop", "mdi:stop-circle-outline"),
    (5, "compose_pull", "pull", "mdi:download-box-outline"),
)

_SYSTEM_COMPOSE_ACTIONS: tuple[tuple[int, str, str, str], ...] = (
    (98, "compose_image_prune", "image prune -f", "mdi:image-remove-outline"),
    (99, "compose_container_prune", "container prune -f", "mdi:trash-can-outline"),
)


def _compose_button_unique_suffix(order: int, translation_key: str, project_key: str) -> str:
    """Return a stable, order-aware suffix for compose project buttons."""
    return f"{order:02d}-{translation_key}-{project_key}"


def _compose_button_object_id(
    coordinator: OMVDataUpdateCoordinator,
    order: int,
    translation_key: str,
    project_key: str,
) -> str:
    """Return an order-aware object id so Home Assistant sorts buttons correctly."""
    action = translation_key.removeprefix("compose_")
    return build_host_object_id(
        coordinator,
        f"{order:02d}",
        "compose",
        project_key,
        action,
    )


def _system_button_unique_suffix(order: int, translation_key: str) -> str:
    """Return a stable, order-aware suffix for global compose buttons."""
    return f"{order:02d}-{translation_key}"


def _system_button_object_id(
    coordinator: OMVDataUpdateCoordinator,
    order: int,
    translation_key: str,
) -> str:
    """Return an order-aware object id for global compose buttons."""
    action = translation_key.removeprefix("compose_")
    return build_host_object_id(coordinator, f"{order:02d}", "compose", action)


def get_expected_button_unique_ids(
    entry: ConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> set[str]:
    """Return the button unique IDs for a config entry."""
    unique_ids = {f"{entry.entry_id}-reboot", f"{entry.entry_id}-shutdown"}
    for project in coordinator.data.get("compose_projects", []):
        if not isinstance(project, dict) or not str(project.get("uuid") or ""):
            continue
        project_key = str(project.get("project_key") or project.get("name") or "")
        if not project_key:
            continue
        for order, translation_key, _command, _icon in _COMPOSE_PROJECT_ACTIONS:
            unique_ids.add(f"{entry.entry_id}-{_compose_button_unique_suffix(order, translation_key, project_key)}")
    if coordinator._has_container_service(coordinator.data.get("service", [])):
        for order, translation_key, _command, _icon in _SYSTEM_COMPOSE_ACTIONS:
            unique_ids.add(f"{entry.entry_id}-{_system_button_unique_suffix(order, translation_key)}")
    return unique_ids


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OMV button entities."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        OMVRebootButton(coordinator),
        OMVShutdownButton(coordinator),
    ]

    for project in coordinator.data.get("compose_projects", []):
        if not isinstance(project, dict) or not str(project.get("uuid") or ""):
            continue
        entities.extend(
            OMVComposeProjectButton(coordinator, project, order, translation_key, command, icon)
            for order, translation_key, command, icon in _COMPOSE_PROJECT_ACTIONS
        )

    if coordinator._has_container_service(coordinator.data.get("service", [])):
        entities.extend(
            OMVComposeSystemButton(coordinator, order, translation_key, command, icon)
            for order, translation_key, command, icon in _SYSTEM_COMPOSE_ACTIONS
        )

    async_add_entities(entities)


class OMVRebootButton(OMVEntity, ButtonEntity):
    """Button to reboot the OMV host."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "reboot"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: OMVDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "reboot")
        self._attr_suggested_object_id = build_host_object_id(coordinator, "reboot")

    async def async_press(self) -> None:
        """Trigger a reboot on the OMV host."""
        await self.coordinator.api.async_call("System", "reboot")


class OMVShutdownButton(OMVEntity, ButtonEntity):
    """Button to shut down the OMV host."""

    _attr_translation_key = "shutdown"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: OMVDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "shutdown")
        self._attr_suggested_object_id = build_host_object_id(coordinator, "shutdown")

    async def async_press(self) -> None:
        """Trigger a shutdown on the OMV host."""
        await self.coordinator.api.async_call("System", "shutdown")


class OMVComposeProjectButton(OMVEntity, ButtonEntity):
    """Button to execute a compose command for one compose project."""

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        project: dict[str, str],
        order: int,
        translation_key: str,
        command: str,
        icon: str,
    ) -> None:
        project_key = str(project.get("project_key") or project.get("name") or "")
        self._project_uuid = str(project.get("uuid") or "")
        self._command = command
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_suggested_object_id = _compose_button_object_id(
            coordinator,
            order,
            translation_key,
            project_key,
        )
        super().__init__(
            coordinator,
            _compose_button_unique_suffix(order, translation_key, project_key),
            device_info=get_compose_project_device_info(coordinator, project),
        )

    async def async_press(self) -> None:
        """Trigger the compose file command in OMV."""
        await self.coordinator.async_execute_compose_command(
            {"uuid": self._project_uuid, "command": self._command},
        )
        await self.coordinator.async_request_refresh()


class OMVComposeSystemButton(OMVEntity, ButtonEntity):
    """Button to execute a global compose/docker maintenance command."""

    def __init__(
        self,
        coordinator: OMVDataUpdateCoordinator,
        order: int,
        translation_key: str,
        command: str,
        icon: str,
    ) -> None:
        self._command = command
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_suggested_object_id = _system_button_object_id(
            coordinator,
            order,
            translation_key,
        )
        super().__init__(coordinator, _system_button_unique_suffix(order, translation_key))

    async def async_press(self) -> None:
        """Trigger a global compose maintenance command in OMV."""
        await self.coordinator.async_execute_compose_command(
            {"command": self._command},
        )
        await self.coordinator.async_request_refresh()

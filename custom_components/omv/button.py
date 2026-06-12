"""Button platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OMVDataUpdateCoordinator
from .entity import (
    OMVEntity,
    build_host_object_id,
    get_compose_project_device_info,
    get_container_device_info,
    get_vm_device_info,
)
from .exceptions import OMVApiError, OMVConnectionError

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
    unique_ids = {
        f"{entry.entry_id}-reboot",
        f"{entry.entry_id}-shutdown",
        f"{entry.entry_id}-apply_config",
    }
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
    for container in coordinator.data.get("compose", []):
        if not isinstance(container, dict):
            continue
        container_key = str(container.get("container_key") or "")
        if container_key:
            unique_ids.add(f"{entry.entry_id}-container_restart-{container_key}")
    for vm in coordinator.data.get("kvm", []):
        if not isinstance(vm, dict):
            continue
        vm_key = str(vm.get("vm_key") or "")
        if vm_key:
            unique_ids.add(f"{entry.entry_id}-vm_restart-{vm_key}")
    for job in coordinator.data.get("rsync", []):
        if not isinstance(job, dict):
            continue
        rsync_key = str(job.get("rsync_key") or "")
        if rsync_key:
            unique_ids.add(f"{entry.entry_id}-rsync_run-{rsync_key}")
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
        OMVApplyConfigButton(coordinator),
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

    for container in coordinator.data.get("compose", []):
        if not isinstance(container, dict) or not str(container.get("container_key") or ""):
            continue
        entities.append(OMVContainerRestartButton(coordinator, container))

    for vm in coordinator.data.get("kvm", []):
        if not isinstance(vm, dict) or not str(vm.get("vm_key") or ""):
            continue
        entities.append(OMVVmRestartButton(coordinator, vm))

    for job in coordinator.data.get("rsync", []):
        if not isinstance(job, dict) or not str(job.get("rsync_key") or ""):
            continue
        entities.append(OMVRsyncRunButton(coordinator, job))

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
        """Trigger a reboot on the OMV host.

        Raises:
            HomeAssistantError: When OMV has pending configuration changes that
                must be applied before rebooting.
        """
        await self.coordinator.async_request_refresh()
        hwinfo = self.coordinator.data.get("hwinfo", {})
        if hwinfo.get("configDirty"):
            modules = ", ".join(hwinfo.get("dirtyModules") or []) or "?"
            raise HomeAssistantError(
                translation_domain="omv",
                translation_key="reboot_blocked_config_dirty",
                translation_placeholders={"modules": modules},
            )
        await self.coordinator.api.async_call("System", "reboot")


class OMVApplyConfigButton(OMVEntity, ButtonEntity):
    """Button to apply pending OMV configuration changes."""

    _attr_translation_key = "apply_config"
    _attr_icon = "mdi:check-network"

    def __init__(self, coordinator: OMVDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "apply_config")
        self._attr_suggested_object_id = build_host_object_id(coordinator, "apply_config")

    async def async_press(self) -> None:
        """Apply pending OMV configuration changes.

        Raises:
            HomeAssistantError: When the Config.applyChanges RPC call fails.
        """
        await self.coordinator.async_request_refresh()
        try:
            await self.coordinator.api.async_apply_config()
        except Exception as err:
            raise HomeAssistantError(
                translation_domain="omv",
                translation_key="apply_config_failed",
            ) from err
        await self.coordinator.async_request_refresh()
        self.coordinator.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": "omv_config_dirty"},
        )


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


class OMVContainerRestartButton(OMVEntity, ButtonEntity):
    """Button to restart one Docker Compose container."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "container_restart"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: OMVDataUpdateCoordinator, container: dict[str, Any]) -> None:
        """Initialize the container restart button.

        Args:
            coordinator: The OMV data update coordinator.
            container: The normalized compose container record.
        """
        container_key = str(container.get("container_key") or "")
        self._container_id = str(container.get("container_id") or container_key)
        self._attr_suggested_object_id = build_host_object_id(
            coordinator,
            "container",
            container.get("name") or container_key,
            "restart",
        )
        super().__init__(
            coordinator,
            f"container_restart-{container_key}",
            device_info=get_container_device_info(coordinator, container),
        )

    async def async_press(self) -> None:
        """Restart this container via Compose.doContainerCommand."""
        await self.coordinator.async_execute_container_command(self._container_id, "restart")
        await self.coordinator.async_request_refresh()


class OMVVmRestartButton(OMVEntity, ButtonEntity):
    """Button to reboot one KVM virtual machine."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "vm_restart"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: OMVDataUpdateCoordinator, vm: dict[str, Any]) -> None:
        """Initialize the VM restart button.

        Args:
            coordinator: The OMV data update coordinator.
            vm: The normalized KVM virtual machine record.
        """
        self._vm_key = str(vm.get("vm_key") or "")
        self._vm = dict(vm)
        self._attr_suggested_object_id = build_host_object_id(
            coordinator,
            "vm",
            vm.get("name") or self._vm_key,
            "restart",
        )
        super().__init__(
            coordinator,
            f"vm_restart-{self._vm_key}",
            device_info=get_vm_device_info(coordinator, vm),
        )

    async def async_press(self) -> None:
        """Reboot this VM via Kvm.doCommand.

        Raises:
            HomeAssistantError: When the Kvm.doCommand RPC call fails.
        """
        try:
            await self.coordinator.async_execute_vm_command(self._vm, "reboot")
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="vm_command_failed",
                translation_placeholders={
                    "resource": str(self._vm.get("name") or self._vm_key),
                    "command": "reboot",
                },
            ) from err
        finally:
            await self.coordinator.async_request_refresh()


class OMVRsyncRunButton(OMVEntity, ButtonEntity):
    """Button to start one OMV rsync job."""

    _attr_translation_key = "rsync_run"
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator: OMVDataUpdateCoordinator, job: dict[str, Any]) -> None:
        """Initialize the rsync run button.

        Args:
            coordinator: The OMV data update coordinator.
            job: The normalized rsync job record.
        """
        self._rsync_key = str(job.get("rsync_key") or "")
        self._job_name = str(job.get("name") or self._rsync_key)
        self._attr_translation_placeholders = {"name": self._job_name}
        self._attr_suggested_object_id = build_host_object_id(
            coordinator,
            "rsync",
            self._job_name,
            "run",
        )
        super().__init__(coordinator, f"rsync_run-{self._rsync_key}")

    async def async_press(self) -> None:
        """Start this rsync job via Rsync.execute (fire-and-forget).

        Raises:
            HomeAssistantError: When the Rsync.execute RPC call fails.
        """
        try:
            await self.coordinator.async_execute_rsync_job(self._rsync_key)
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rsync_execute_failed",
                translation_placeholders={"name": self._job_name},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()

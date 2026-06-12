"""Switch platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OMVDataUpdateCoordinator
from .entity import (
    OMVEntity,
    build_host_object_id,
    get_container_device_info,
    get_vm_device_info,
)
from .exceptions import OMVApiError, OMVConnectionError


def get_expected_switch_unique_ids(coordinator: OMVDataUpdateCoordinator) -> set[str]:
    """Return the switch unique IDs for the current runtime data."""
    entry_id = coordinator.config_entry.entry_id
    unique_ids: set[str] = set()
    for container in coordinator.data.get("compose", []):
        if not isinstance(container, dict):
            continue
        container_key = str(container.get("container_key") or "")
        if container_key:
            unique_ids.add(f"{entry_id}-container-{container_key}")
    for vm in coordinator.data.get("kvm", []):
        if not isinstance(vm, dict):
            continue
        vm_key = str(vm.get("vm_key") or "")
        if vm_key:
            unique_ids.add(f"{entry_id}-vm-{vm_key}")
    return unique_ids


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OMV container and VM switches."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    for container in coordinator.data.get("compose", []):
        if not isinstance(container, dict) or not str(container.get("container_key") or ""):
            continue
        entities.append(OMVContainerSwitch(coordinator, container))
    for vm in coordinator.data.get("kvm", []):
        if not isinstance(vm, dict) or not str(vm.get("vm_key") or ""):
            continue
        entities.append(OMVVmSwitch(coordinator, vm))
    async_add_entities(entities)


class OMVContainerSwitch(OMVEntity, SwitchEntity):
    """Switch to start or stop a Docker Compose container."""

    _attr_name = None
    _attr_icon = "mdi:docker"

    def __init__(self, coordinator: OMVDataUpdateCoordinator, container: dict[str, Any]) -> None:
        """Initialize the container switch.

        Args:
            coordinator: The OMV data update coordinator.
            container: The normalized compose container record.
        """
        self._container_key = str(container.get("container_key") or "")
        self._container_id = str(container.get("container_id") or self._container_key)
        self._attr_is_on = bool(container.get("running"))
        super().__init__(
            coordinator,
            f"container-{self._container_key}",
            device_info=get_container_device_info(coordinator, container),
        )
        self._attr_suggested_object_id = build_host_object_id(
            coordinator,
            "container",
            container.get("name") or self._container_key,
        )

    def _get_container(self) -> dict[str, Any] | None:
        """Return the current compose container record from coordinator data, if present."""
        for item in self.coordinator.data.get("compose", []):
            if isinstance(item, dict) and str(item.get("container_key") or "") == self._container_key:
                return item
        return None

    def _handle_coordinator_update(self) -> None:
        """Sync the optimistic switch state with the latest coordinator data."""
        container = self._get_container()
        if container is not None:
            self._attr_is_on = bool(container.get("running"))
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the container."""
        await self._async_send_command("start", optimistic_state=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the container."""
        await self._async_send_command("stop", optimistic_state=False)

    async def _async_send_command(self, command: str, *, optimistic_state: bool) -> None:
        """Send a start/stop command for this container and refresh coordinator data.

        Args:
            command: The Compose container command to run ("start" or "stop").
            optimistic_state: The state to apply immediately, before the RPC confirms it.

        Raises:
            HomeAssistantError: When the Compose.doContainerCommand RPC call fails.
        """
        self._attr_is_on = optimistic_state
        self.async_write_ha_state()
        try:
            await self.coordinator.async_execute_container_command(self._container_id, command)
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_command_failed",
                translation_placeholders={"resource": self._container_key, "command": command},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()


class OMVVmSwitch(OMVEntity, SwitchEntity):
    """Switch to start or stop a KVM virtual machine.

    Turning on issues ``poweron``; turning off issues ``poweroff`` (a graceful
    virsh shutdown). The destructive ``force`` command (virsh destroy) is
    intentionally not exposed as an entity action.
    """

    _attr_name = None
    _attr_icon = "mdi:server"

    def __init__(self, coordinator: OMVDataUpdateCoordinator, vm: dict[str, Any]) -> None:
        """Initialize the VM switch.

        Args:
            coordinator: The OMV data update coordinator.
            vm: The normalized KVM virtual machine record.
        """
        self._vm_key = str(vm.get("vm_key") or "")
        self._vm = dict(vm)
        self._attr_is_on = bool(vm.get("running"))
        super().__init__(
            coordinator,
            f"vm-{self._vm_key}",
            device_info=get_vm_device_info(coordinator, vm),
        )
        self._attr_suggested_object_id = build_host_object_id(
            coordinator,
            "vm",
            vm.get("name") or self._vm_key,
        )

    def _get_vm(self) -> dict[str, Any] | None:
        """Return the current KVM record from coordinator data, if present."""
        for item in self.coordinator.data.get("kvm", []):
            if isinstance(item, dict) and str(item.get("vm_key") or "") == self._vm_key:
                return item
        return None

    def _handle_coordinator_update(self) -> None:
        """Sync the optimistic switch state with the latest coordinator data."""
        vm = self._get_vm()
        if vm is not None:
            self._vm = dict(vm)
            self._attr_is_on = bool(vm.get("running"))
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the virtual machine."""
        await self._async_send_command("poweron", optimistic_state=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the virtual machine gracefully."""
        await self._async_send_command("poweroff", optimistic_state=False)

    async def _async_send_command(self, command: str, *, optimistic_state: bool) -> None:
        """Send a power command for this VM and refresh coordinator data.

        Args:
            command: The Kvm.doCommand command to run ("poweron" or "poweroff").
            optimistic_state: The state to apply immediately, before the RPC confirms it.

        Raises:
            HomeAssistantError: When the Kvm.doCommand RPC call fails.
        """
        self._attr_is_on = optimistic_state
        self.async_write_ha_state()
        try:
            await self.coordinator.async_execute_vm_command(self._vm, command)
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="vm_command_failed",
                translation_placeholders={
                    "resource": str(self._vm.get("name") or self._vm_key),
                    "command": command,
                },
            ) from err
        finally:
            await self.coordinator.async_request_refresh()

"""Repairs for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import OMVDataUpdateCoordinator

_REBOOT_REQUIRED_ISSUE = "reboot_required"


def get_reboot_required_issue_id(entry_id: str) -> str:
    """Return the issue ID for a config entry's reboot repair."""
    return f"{_REBOOT_REQUIRED_ISSUE}_{entry_id}"


@callback
def async_delete_reboot_repair_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the reboot repair issue for a config entry."""
    ir.async_delete_issue(hass, DOMAIN, get_reboot_required_issue_id(entry_id))


@callback
def async_sync_reboot_repair_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create or delete the reboot repair based on current OMV state."""
    coordinator = entry.runtime_data
    if not isinstance(coordinator, OMVDataUpdateCoordinator):
        return

    hwinfo = coordinator.data.get("hwinfo", {})
    issue_id = get_reboot_required_issue_id(entry.entry_id)
    pending_updates = int(hwinfo.get("availablePkgUpdates", 0))
    reboot_required = bool(hwinfo.get("rebootRequired", False))
    config_dirty = bool(hwinfo.get("configDirty", False))

    if pending_updates == 0 and reboot_required and not config_dirty:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            data={"entry_id": entry.entry_id},
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_REBOOT_REQUIRED_ISSUE,
            translation_placeholders={"title": entry.title},
        )
        return

    ir.async_delete_issue(hass, DOMAIN, issue_id)


class OMVRebootRepairFlow(ConfirmRepairFlow):
    """Repair flow that reboots the OMV host."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the reboot repair flow."""
        self._entry = entry
        super().__init__()

    @property
    def _coordinator(self) -> OMVDataUpdateCoordinator:
        """Return the runtime coordinator for the config entry."""
        coordinator = self._entry.runtime_data
        if not isinstance(coordinator, OMVDataUpdateCoordinator):
            raise ValueError("OMV config entry is not loaded")
        return coordinator

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        """Handle the confirmation step for the reboot repair."""
        if user_input is None:
            return await super().async_step_confirm(user_input)

        coordinator = self._coordinator
        await coordinator.async_request_refresh()
        hwinfo = coordinator.data.get("hwinfo", {})

        if bool(hwinfo.get("configDirty", False)):
            modules = ", ".join(hwinfo.get("dirtyModules") or []) or "?"
            async_sync_reboot_repair_issue(self.hass, self._entry)
            return self.async_abort(
                reason="config_dirty",
                description_placeholders={"modules": modules},
            )

        if int(hwinfo.get("availablePkgUpdates", 0)) > 0 or not bool(hwinfo.get("rebootRequired", False)):
            async_sync_reboot_repair_issue(self.hass, self._entry)
            return self.async_create_entry(data={})

        await coordinator.api.async_call("System", "reboot")
        if isinstance(hwinfo, dict):
            hwinfo["rebootRequired"] = False
        coordinator.async_update_listeners()
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the fix flow for a reboot repair issue."""
    if data is None or not isinstance(data.get("entry_id"), str):
        raise ValueError(f"Missing config entry for repair {issue_id}")

    entry = hass.config_entries.async_get_entry(data["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        raise ValueError(f"Unknown repair {issue_id}")

    return OMVRebootRepairFlow(entry)

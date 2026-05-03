"""Update platform for the OpenMediaVault integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity, build_host_object_id


def get_expected_update_unique_ids(entry: ConfigEntry) -> set[str]:
    """Return the update entity unique IDs for a config entry.

    Args:
        entry: The config entry.

    Returns:
        Set of unique ID strings for all update entities.
    """
    return {f"{entry.entry_id}-omv_system_update"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the OMV update entity."""
    coordinator: OMVDataUpdateCoordinator = entry.runtime_data
    async_add_entities([OMVUpdateEntity(coordinator)])


class OMVUpdateEntity(OMVEntity, UpdateEntity):
    """Update entity representing available OMV package updates.

    Reports the currently installed OMV version as installed_version and
    synthesises a differing latest_version string whenever package updates
    are pending so that Home Assistant marks the entity state as 'on'
    (update available). The release_url points to the OMV web interface so
    users can open the dashboard directly from the HA update card.
    """

    _attr_translation_key = "omv_system_update"
    _attr_title = "OpenMediaVault"
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: OMVDataUpdateCoordinator) -> None:
        """Initialize the OMV update entity.

        Args:
            coordinator: The OMV data update coordinator instance.
        """
        super().__init__(coordinator, "omv_system_update")
        self._attr_suggested_object_id = build_host_object_id(coordinator, "system_update")

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed OMV version.

        Returns:
            The OMV version string (e.g. '7.7.24-7'), or None when unknown.
        """
        hwinfo = self.coordinator.data.get("hwinfo", {})
        version = hwinfo.get("version")
        if not version or str(version).lower() == "unknown":
            return None
        return str(version)

    @property
    def latest_version(self) -> str | None:
        """Return a version string representing the latest available state.

        When package updates are pending, returns a synthetic string that
        differs from installed_version (e.g. '7.7.24-7 (+3 packages)') so
        that Home Assistant transitions the entity state to 'on'. When the
        system is up-to-date, returns installed_version unchanged.

        Returns:
            A version string, or None when installed_version is unknown.
        """
        installed = self.installed_version
        if installed is None:
            return None
        hwinfo = self.coordinator.data.get("hwinfo", {})
        n = int(hwinfo.get("availablePkgUpdates", 0))
        if n > 0:
            return f"{installed} (+{n} packages)"
        return installed

    @property
    def release_url(self) -> str | None:
        """Return the URL to the OMV update management page.

        Returns:
            The URL of the OMV update management page.
        """
        return f"{self.coordinator.api.base_url}/#/system/updatemgmt/updates"

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Trigger installation of all available OMV package updates.

        Calls OMV's Apt.upgrade RPC to start the background upgrade task,
        then schedules a coordinator refresh so the installed_version and
        latest_version attributes reflect the new state. A reboot may be
        required after the upgrade; use the dedicated Reboot button entity.

        Args:
            version: Target version string (unused; OMV upgrades all packages).
            backup: Whether to create a backup before installing (not supported).
            **kwargs: Additional keyword arguments (unused).
        """
        await self.coordinator.api.async_call("Apt", "upgrade")
        await self.coordinator.async_request_refresh()

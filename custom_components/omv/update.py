"""Update platform for the OpenMediaVault integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OMVDataUpdateCoordinator
from .entity import OMVEntity, build_host_object_id
from .exceptions import OMVConnectionError

_LOGGER = logging.getLogger(__name__)

_INSTALL_POLL_INTERVAL = 10  # seconds between Exec.isRunning polls
_INSTALL_TIMEOUT = 600  # maximum seconds per background process step (10 minutes)
_MAX_POLLS = _INSTALL_TIMEOUT // _INSTALL_POLL_INTERVAL


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
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES

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
        # No pending package updates — but if a reboot is still required
        # (e.g. after a kernel update) keep the entity in state 'on' so the
        # user sees the pending action.  A synthetic suffix keeps latest_version
        # different from installed_version while conveying the reason.
        if hwinfo.get("rebootRequired"):
            return f"{installed} (reboot required)"
        return installed

    @property
    def release_summary(self) -> str | None:
        """Return a short ≤200-character preview of pending package updates.

        Shows the first one or two packages followed by a count of remaining
        packages. Stays well within Home Assistant's 255-character attribute
        limit so the value is never silently truncated.

        Returns:
            A single-line preview string, or None when no package details are
            available.
        """
        packages: list[dict[str, Any]] = self.coordinator.data.get("upgradedList", [])
        if not packages:
            return None

        previews: list[str] = []
        for pkg in packages[:2]:
            name = str(pkg.get("name") or pkg.get("package") or "?")
            version = str(pkg.get("version") or "")
            previews.append(f"{name} {version}".strip() if version else name)

        result = ", ".join(previews)
        remaining = len(packages) - len(previews)
        if remaining > 0:
            result += f" … +{remaining} more"
        return result[:200]

    async def async_release_notes(self) -> str | None:
        """Return the full Markdown release notes with all pending package updates.

        Each package block contains the package name, new version, and —
        when present — the summary description. Packages are separated by
        a blank line so Home Assistant renders each as a distinct paragraph.

        Returns:
            A Markdown string with one block per package, or None when no
            package details are available.
        """
        packages: list[dict[str, Any]] = self.coordinator.data.get("upgradedList", [])
        if not packages:
            return None

        blocks: list[str] = []
        for pkg in packages:
            name = str(pkg.get("name") or pkg.get("package") or "?")
            version = str(pkg.get("version") or "")
            # Wrap version in backticks so that ~ in Debian version strings
            # (e.g. ~debian.12~bookworm) is not rendered as Markdown strikethrough.
            line = f"**{name}**" + (f" `{version}`" if version else "")
            summary = str(pkg.get("summary") or "").strip()
            if summary:
                # Two trailing spaces force a Markdown line break without
                # inserting a blank paragraph between name/version and description.
                line += f"  \n{summary}"
            blocks.append(line)

        return "\n\n".join(blocks)

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return additional state attributes for the update entity.

        Returns:
            A dict with ``reboot_required`` indicating whether the OMV host
            needs a reboot to fully apply installed or upgraded packages.
        """
        hwinfo = self.coordinator.data.get("hwinfo", {})
        return {"reboot_required": bool(hwinfo.get("rebootRequired", False))}

    @property
    def release_url(self) -> str | None:
        """Return the URL to the OMV update management page.

        Returns:
            The URL of the OMV update management page.
        """
        return f"{self.coordinator.api.base_url}/#/system/updatemgmt/updates"

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Trigger installation of all available OMV package updates, or reboot.

        When package updates are pending, mirrors the OMV web UI workflow:
          1. Apt.update  — refreshes the apt package cache (apt-get update).
          2. Apt.upgrade — runs the full dist-upgrade (apt-get dist-upgrade).
          3. Apt.update  — re-runs apt-get update so OMV recalculates
                           availablePkgUpdates (stored in OMV's config DB and
                           updated only by omv-aptlist, which Apt.update
                           triggers). Without this step the coordinator would
                           refresh with a stale count and the entity would
                           continue to show updates as available.

        When no package updates are pending but a reboot is required (e.g. after
        a kernel upgrade), the action calls System.reboot instead.  This makes
        the Install button behave as a "Reboot" button in that scenario, which
        is the only remaining action needed to fully apply the changes.

        Each apt step starts an OMV background process that is polled via
        Exec.isRunning until it completes or the 10-minute timeout is reached.
        HA manages the in_progress indicator automatically for the lifetime of
        this coroutine; if the upgrade step fails, the exception propagates to
        HA. A failure in the post-upgrade Apt.update is logged and swallowed
        (the packages are installed; only the displayed count may be stale).

        Args:
            version: Target version string (unused; OMV upgrades all packages).
            backup: Whether to create a backup before installing (not supported).
            **kwargs: Additional keyword arguments (unused).
        """
        # Fetch fresh data from OMV before making decisions — the cached
        # coordinator data can be up to 60 s old, so configDirty / package
        # counts may already have changed since the last poll.
        await self.coordinator.async_request_refresh()
        hwinfo = self.coordinator.data.get("hwinfo", {})
        n_updates = int(hwinfo.get("availablePkgUpdates", 0))
        reboot_required = bool(hwinfo.get("rebootRequired", False))

        # B2: Block install when OMV has unapplied configuration changes.
        if hwinfo.get("configDirty"):
            raise HomeAssistantError(
                translation_domain="omv",
                translation_key="config_dirty",
            )

        # Reboot-only case: no packages to install, just a pending reboot.
        if n_updates == 0 and reboot_required:
            _LOGGER.info("No package updates pending; triggering system reboot via OMV")
            await self.coordinator.api.async_call("System", "reboot")
            # Optimistically clear the reboot flag so the update card transitions
            # to 'off' immediately instead of waiting for the next coordinator poll
            # (up to 60 s later).  The coordinator will confirm the real state on
            # the next successful refresh once the host is back online.
            if isinstance(self.coordinator.data.get("hwinfo"), dict):
                self.coordinator.data["hwinfo"]["rebootRequired"] = False
            self.coordinator.async_update_listeners()
            return

        # Step 1: refresh apt cache so dist-upgrade sees current package state
        update_file = await self.coordinator.api.async_call("Apt", "update")
        await self._wait_for_bgproc(update_file)
        # Step 2: run the actual dist-upgrade
        upgrade_file = await self.coordinator.api.async_call("Apt", "upgrade")
        await self._wait_for_bgproc(upgrade_file)
        # Step 3: refresh apt cache again so OMV recalculates availablePkgUpdates
        # (omv-aptlist is only triggered by Apt.update, not by Apt.upgrade).
        try:
            post_update_file = await self.coordinator.api.async_call("Apt", "update")
            await self._wait_for_bgproc(post_update_file)
        except Exception:
            _LOGGER.warning("Post-upgrade Apt.update failed; availablePkgUpdates may be stale")
        # Step 4: pull fresh data so the entity reflects the new package state
        await self.coordinator.async_request_refresh()

    async def _wait_for_bgproc(self, filename: Any) -> None:
        """Poll Exec.isRunning until the OMV background process finishes.

        If the background process reports an error, OMV raises a TraceException
        which is propagated to the caller as OMVApiError so HA can mark the
        install as failed.  If *filename* is not a string the call is silently
        skipped (defensive guard for unexpected RPC responses).

        OMV deletes the bgproc status file as soon as the process completes.
        Any subsequent Exec.isRunning call then returns HTTP 500 because the
        file no longer exists.  HTTP 500 is therefore treated as "process
        finished successfully" rather than a real connection error.

        Args:
            filename: The bgproc status filename returned by an OMV execBgProc
                RPC call (e.g. Apt.update / Apt.upgrade).
        """
        if not isinstance(filename, str) or not filename:
            return
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_INSTALL_POLL_INTERVAL)
            try:
                result = await self.coordinator.api.async_call(
                    "Exec", "isRunning", {"filename": filename}, max_retries=0
                )
            except OMVConnectionError as err:
                if "HTTP 500" in str(err):
                    # OMV deletes the bgproc status file once the process
                    # completes; the next Exec.isRunning call then returns
                    # HTTP 500 because the file is gone — treat as done.
                    _LOGGER.debug(
                        "Exec.isRunning returned HTTP 500 for %s — bgproc completed",
                        filename,
                    )
                    break
                raise
            if isinstance(result, dict) and not result.get("running", True):
                break

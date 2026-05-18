"""Tests for OMV update entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.update import UpdateEntityFeature

from custom_components.omv.const import DOMAIN
from custom_components.omv.exceptions import OMVConnectionError
from custom_components.omv.update import (
    OMVUpdateEntity,
    async_setup_entry,
    get_expected_update_unique_ids,
)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_one_update_entity(coordinator, config_entry) -> None:
    """Test that async_setup_entry registers exactly one update entity."""
    added: list = []

    def add_entities(entities: list) -> None:
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 1
    assert isinstance(added[0], OMVUpdateEntity)


def test_installed_version(coordinator) -> None:
    """Test installed_version returns the OMV version from hwinfo."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.installed_version == "8.1.2-1"


def test_latest_version_with_updates(coordinator) -> None:
    """Test latest_version returns a synthetic string when updates are pending."""
    # sample_data has availablePkgUpdates=3, pkgUpdatesAvailable=True
    entity = OMVUpdateEntity(coordinator)
    assert entity.latest_version == "8.1.2-1 (+3 packages)"
    assert entity.latest_version != entity.installed_version


def test_latest_version_no_updates(coordinator, sample_data) -> None:
    """Test latest_version equals installed_version when no updates are available."""
    sample_data["hwinfo"]["availablePkgUpdates"] = 0
    sample_data["hwinfo"]["pkgUpdatesAvailable"] = False
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.latest_version == entity.installed_version


def test_installed_version_unknown(coordinator, sample_data) -> None:
    """Test installed_version returns None when version is 'unknown'."""
    sample_data["hwinfo"]["version"] = "unknown"
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.installed_version is None
    assert entity.latest_version is None


def test_release_url(coordinator) -> None:
    """Test release_url points to the OMV update management page."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.release_url == "http://192.168.1.10:80/#/system/updatemgmt/updates"


def test_supported_features_includes_install_and_release_notes(coordinator) -> None:
    """Test that INSTALL and RELEASE_NOTES features are declared."""
    entity = OMVUpdateEntity(coordinator)
    assert entity._attr_supported_features & UpdateEntityFeature.INSTALL
    assert entity._attr_supported_features & UpdateEntityFeature.RELEASE_NOTES


@pytest.mark.asyncio
async def test_async_install_runs_update_then_upgrade(coordinator) -> None:
    """Test async_install calls Apt.update, Apt.upgrade, Apt.update, then refresh.

    The post-upgrade Apt.update is required so OMV recalculates
    availablePkgUpdates (via omv-aptlist) before the coordinator refreshes.
    """
    apt_update_file = "/tmp/bgstatus_update"
    apt_upgrade_file = "/tmp/bgstatus_upgrade"
    not_running = {"filename": apt_update_file, "running": False}

    call_log: list[tuple] = []

    async def _mock_call(service: str, method: str, params: dict | None = None, **_: object) -> object:
        call_log.append((service, method))
        if service == "Apt" and method == "update":
            return apt_update_file
        if service == "Apt" and method == "upgrade":
            return apt_upgrade_file
        if service == "Exec" and method == "isRunning":
            return not_running
        return None

    coordinator.api.async_call = _mock_call
    coordinator.async_request_refresh = AsyncMock()

    entity = OMVUpdateEntity(coordinator)

    with patch("custom_components.omv.update.asyncio.sleep", new_callable=AsyncMock):
        await entity.async_install(version=None, backup=False)

    # Apt.update must be called twice: once before upgrade, once after
    assert call_log.count(("Apt", "update")) == 2
    assert ("Apt", "upgrade") in call_log
    assert ("Exec", "isRunning") in call_log
    # upgrade sequence: update → upgrade → update
    apt_calls = [c for c in call_log if c[0] == "Apt"]
    assert apt_calls == [("Apt", "update"), ("Apt", "upgrade"), ("Apt", "update")]
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_install_raises_on_apt_update_error(coordinator) -> None:
    """Test async_install propagates Apt.update errors to the caller."""
    coordinator.api.async_call = AsyncMock(side_effect=Exception("update failed"))

    entity = OMVUpdateEntity(coordinator)

    with pytest.raises(Exception, match="update failed"):
        await entity.async_install(version=None, backup=False)


@pytest.mark.asyncio
async def test_async_install_raises_on_exec_isrunning_error(coordinator) -> None:
    """Test async_install propagates non-HTTP-500 Exec.isRunning errors to the caller."""

    async def _mock_call(service: str, method: str, params: dict | None = None, **_: object) -> object:
        if service == "Apt":
            return "/tmp/bgstatus"
        raise Exception("apt-get dist-upgrade failed with exit code 1")

    coordinator.api.async_call = _mock_call

    entity = OMVUpdateEntity(coordinator)

    with (
        patch("custom_components.omv.update.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Exception, match="apt-get dist-upgrade failed"),
    ):
        await entity.async_install(version=None, backup=False)


@pytest.mark.asyncio
async def test_async_install_treats_http500_from_bgproc_as_done(coordinator) -> None:
    """Test that HTTP 500 from Exec.isRunning is treated as bgproc completion, not an error.

    OMV deletes the bgproc status file once the process completes.
    A subsequent Exec.isRunning call returns HTTP 500 (file not found).
    This must NOT propagate as an error — it means the upgrade succeeded.
    """

    async def _mock_call(service: str, method: str, params: dict | None = None, **_: object) -> object:
        if service == "Apt":
            return "/tmp/bgstatus"
        raise OMVConnectionError("Failed to reach OMV after 0 retries: OMV returned HTTP 500")

    coordinator.api.async_call = _mock_call
    coordinator.async_request_refresh = AsyncMock()

    entity = OMVUpdateEntity(coordinator)

    with patch("custom_components.omv.update.asyncio.sleep", new_callable=AsyncMock):
        await entity.async_install(version=None, backup=False)

    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_install_stops_polling_after_timeout(coordinator) -> None:
    """Test _wait_for_bgproc stops after _MAX_POLLS when process never finishes."""
    still_running = {"filename": "/tmp/bgstatus", "running": True}

    async def _mock_call(service: str, method: str, params: dict | None = None, **_: object) -> object:
        if service == "Apt":
            return "/tmp/bgstatus"
        return still_running

    coordinator.api.async_call = _mock_call
    coordinator.async_request_refresh = AsyncMock()

    entity = OMVUpdateEntity(coordinator)

    poll_count = 0

    async def _counting_sleep(delay: float) -> None:
        nonlocal poll_count
        poll_count += 1

    with (
        patch("custom_components.omv.update._MAX_POLLS", 2),
        patch("custom_components.omv.update.asyncio.sleep", side_effect=_counting_sleep),
    ):
        await entity.async_install(version=None, backup=False)

    # 2 polls per bgproc step x 3 steps (update/upgrade/update) = 6 total sleeps at most
    assert poll_count <= 6


@pytest.mark.asyncio
async def test_async_install_post_update_error_is_swallowed(coordinator) -> None:
    """Test that a failure in the post-upgrade Apt.update does not propagate.

    The packages are installed at this point; only the displayed count may be
    temporarily stale. The install must still be reported as successful.
    """
    apt_calls: list[str] = []

    async def _mock_call(service: str, method: str, params: dict | None = None, **_: object) -> object:
        if service == "Apt" and method == "upgrade":
            return "/tmp/bgstatus_upgrade"
        if service == "Apt" and method == "update":
            apt_calls.append("update")
            if len(apt_calls) >= 2:
                # Second Apt.update (post-upgrade) fails
                raise Exception("network temporarily unavailable")
            return "/tmp/bgstatus_update"
        # Exec.isRunning → not running
        return {"running": False}

    coordinator.api.async_call = _mock_call
    coordinator.async_request_refresh = AsyncMock()

    entity = OMVUpdateEntity(coordinator)

    # Must NOT raise even though post-upgrade Apt.update fails
    with patch("custom_components.omv.update.asyncio.sleep", new_callable=AsyncMock):
        await entity.async_install(version=None, backup=False)

    coordinator.async_request_refresh.assert_awaited_once()


def test_get_expected_update_unique_ids(config_entry) -> None:
    """Test get_expected_update_unique_ids returns the correct unique ID set."""
    result = get_expected_update_unique_ids(config_entry)
    assert result == {f"{config_entry.entry_id}-omv_system_update"}


def test_unique_id(coordinator, config_entry) -> None:
    """Test unique_id is scoped to the config entry."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.unique_id == f"{config_entry.entry_id}-omv_system_update"


def test_device_info_is_hub_device(coordinator, config_entry) -> None:
    """Test that the entity is attached to the hub device, not a disk."""
    entity = OMVUpdateEntity(coordinator)
    assert entity.device_info is not None
    assert (DOMAIN, config_entry.entry_id) in entity.device_info["identifiers"]


def test_title(coordinator) -> None:
    """Test title is set to 'OpenMediaVault'."""
    entity = OMVUpdateEntity(coordinator)
    assert entity._attr_title == "OpenMediaVault"


def test_suggested_object_id(coordinator) -> None:
    """Test suggested_object_id includes hostname and system_update."""
    entity = OMVUpdateEntity(coordinator)
    assert "nas" in entity._attr_suggested_object_id
    assert "system_update" in entity._attr_suggested_object_id


def test_release_summary_no_packages(coordinator, sample_data) -> None:
    """Test release_summary returns None when no package details are available."""
    sample_data["upgradedList"] = []
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.release_summary is None


def test_release_summary_with_packages(coordinator, sample_data) -> None:
    """Test release_summary returns a plain-text preview with name and version."""
    sample_data["upgradedList"] = [
        {
            "name": "docker-ce",
            "version": "5:29.4.2-2~debian.12~bookworm",
            "summary": "Docker: the open-source application container engine",
            "maintainer": "Docker <support@docker.com>",
            "homepage": "https://www.docker.com",
            "repository": "Docker CE/bookworm",
            "installedsize": 22735244,
        }
    ]
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    summary = entity.release_summary

    assert summary is not None
    assert "docker-ce" in summary
    assert "5:29.4.2-2~debian.12~bookworm" in summary
    # release_summary is a compact preview — no Markdown formatting
    assert "**" not in summary
    assert len(summary) <= 200


def test_release_summary_multiple_packages_shows_remaining(coordinator, sample_data) -> None:
    """Test release_summary shows first two packages and a count of remaining ones."""
    sample_data["upgradedList"] = [
        {"name": "pkg-a", "version": "1.0"},
        {"name": "pkg-b", "version": "2.0"},
        {"name": "pkg-c", "version": "3.0"},
    ]
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    summary = entity.release_summary

    assert summary is not None
    assert "pkg-a" in summary
    assert "pkg-b" in summary
    assert "+1" in summary
    assert "pkg-c" not in summary


def test_release_summary_no_version(coordinator, sample_data) -> None:
    """Test release_summary uses only the name when version is absent."""
    sample_data["upgradedList"] = [{"name": "minimal-pkg", "installedsize": 0}]
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.release_summary == "minimal-pkg"


@pytest.mark.asyncio
async def test_async_release_notes_returns_none_when_no_packages(coordinator, sample_data) -> None:
    """Test async_release_notes returns None when upgradedList is empty."""
    sample_data["upgradedList"] = []
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    result = await entity.async_release_notes()
    assert result is None


@pytest.mark.asyncio
async def test_async_release_notes_returns_markdown(coordinator, sample_data) -> None:
    """Test async_release_notes returns Markdown with name, version, and description."""
    sample_data["upgradedList"] = [
        {
            "name": "docker-ce",
            "version": "5:29.4.2-2~debian.12~bookworm",
            "summary": "Docker: the open-source application container engine",
        },
        {
            "name": "curl",
            "version": "8.0.1",
            "summary": "Command line tool for transferring data with URLs",
        },
    ]
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    notes = await entity.async_release_notes()

    assert notes is not None
    assert "**docker-ce**" in notes
    assert "`5:29.4.2-2~debian.12~bookworm`" in notes
    assert "Docker: the open-source application container engine" in notes
    assert "**curl**" in notes
    assert "`8.0.1`" in notes
    # Two packages separated by blank line
    assert "\n\n" in notes


@pytest.mark.asyncio
async def test_async_release_notes_omits_empty_summary(coordinator, sample_data) -> None:
    """Test async_release_notes skips the summary line when it is absent."""
    sample_data["upgradedList"] = [{"name": "libssl", "version": "3.0.1"}]
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    notes = await entity.async_release_notes()

    assert notes is not None
    assert "**libssl** `3.0.1`" in notes
    # No trailing newline from missing summary
    assert notes.strip() == "**libssl** `3.0.1`"


@pytest.mark.asyncio
async def test_async_install_raises_when_config_dirty(coordinator, sample_data) -> None:
    """Test async_install raises HomeAssistantError when configDirty is True."""
    from homeassistant.exceptions import HomeAssistantError

    sample_data["hwinfo"]["configDirty"] = True
    coordinator.data = sample_data
    coordinator.api.async_call = AsyncMock()

    entity = OMVUpdateEntity(coordinator)

    with pytest.raises(HomeAssistantError, match="ausstehende"):
        await entity.async_install(version=None, backup=False)
    """Test extra_state_attributes returns reboot_required=False when no reboot needed."""
    sample_data["hwinfo"]["rebootRequired"] = False
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.extra_state_attributes == {"reboot_required": False}


def test_extra_state_attributes_reboot_required(coordinator, sample_data) -> None:
    """Test extra_state_attributes returns reboot_required=True after an upgrade."""
    sample_data["hwinfo"]["rebootRequired"] = True
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.extra_state_attributes == {"reboot_required": True}


def test_latest_version_reboot_required_no_updates(coordinator, sample_data) -> None:
    """Test latest_version differs from installed when reboot is required but no pkg updates."""
    sample_data["hwinfo"]["availablePkgUpdates"] = 0
    sample_data["hwinfo"]["rebootRequired"] = True
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.latest_version != entity.installed_version
    assert entity.latest_version is not None
    assert "reboot required" in (entity.latest_version or "")


def test_latest_version_no_reboot_no_updates(coordinator, sample_data) -> None:
    """Test latest_version equals installed_version when no reboot and no updates pending."""
    sample_data["hwinfo"]["availablePkgUpdates"] = 0
    sample_data["hwinfo"]["rebootRequired"] = False
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.latest_version == entity.installed_version


@pytest.mark.asyncio
async def test_async_install_reboots_when_no_pkg_updates_but_reboot_required(coordinator, sample_data) -> None:
    """Test async_install calls System.reboot when only a reboot is pending."""
    sample_data["hwinfo"]["availablePkgUpdates"] = 0
    sample_data["hwinfo"]["rebootRequired"] = True
    coordinator.data = sample_data

    call_log: list[tuple] = []

    async def _mock_call(service: str, method: str, params: dict | None = None, **_: object) -> object:
        call_log.append((service, method))
        return None

    coordinator.api.async_call = _mock_call
    coordinator.async_update_listeners = MagicMock()

    entity = OMVUpdateEntity(coordinator)
    await entity.async_install(version=None, backup=False)

    assert ("System", "reboot") in call_log
    # apt workflow must NOT be triggered in this case
    assert ("Apt", "update") not in call_log
    assert ("Apt", "upgrade") not in call_log
    # rebootRequired must be cleared optimistically so the card goes to 'off' immediately
    assert coordinator.data["hwinfo"]["rebootRequired"] is False
    coordinator.async_update_listeners.assert_called_once()

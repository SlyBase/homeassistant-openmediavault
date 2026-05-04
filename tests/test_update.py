"""Tests for OMV update entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


def test_supported_features_includes_install(coordinator) -> None:
    """Test that UpdateEntityFeature.INSTALL is declared."""
    entity = OMVUpdateEntity(coordinator)
    assert entity._attr_supported_features & UpdateEntityFeature.INSTALL


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
    """Test release_summary returns a formatted text block for each package."""
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
    # Version is wrapped in backticks to prevent ~ from being parsed as Markdown strikethrough.
    assert "**docker-ce** `5:29.4.2-2~debian.12~bookworm`" in summary
    assert "Docker: the open-source application container engine" in summary
    assert "Betreuer: Docker <support@docker.com>" in summary
    assert "Homepage: https://www.docker.com" in summary
    assert "Quelle: Docker CE/bookworm" in summary
    assert "21.68 MiB" in summary
    # Lines are separated by hard Markdown line-breaks (two trailing spaces).
    assert "  \n" in summary


def test_release_summary_skips_blank_optional_fields(coordinator, sample_data) -> None:
    """Test release_summary omits lines for blank optional fields."""
    sample_data["upgradedList"] = [{"name": "minimal-pkg", "version": "1.0", "installedsize": 0}]
    coordinator.data = sample_data

    entity = OMVUpdateEntity(coordinator)
    assert entity.release_summary == "**minimal-pkg** `1.0`"

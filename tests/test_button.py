"""Tests for OMV button entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.omv.button import (
    OMVApplyConfigButton,
    OMVComposeProjectButton,
    OMVComposeSystemButton,
    OMVContainerRestartButton,
    OMVCronRunButton,
    OMVRebootButton,
    OMVRsyncRunButton,
    OMVShutdownButton,
    OMVStandbyButton,
    OMVVmRestartButton,
    OMVWolButton,
    OMVZfsScrubButton,
    async_setup_entry,
    get_expected_button_unique_ids,
)
from custom_components.omv.const import CONF_SELECTED_CRON_JOBS
from custom_components.omv.exceptions import OMVApiError


@pytest.mark.asyncio
async def test_async_setup_entry_adds_buttons(coordinator, config_entry) -> None:
    """Test button platform setup."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 29
    assert added[2].unique_id.endswith("standby")
    assert added[4].unique_id.endswith("01-compose_up-paperless")
    assert added[5].unique_id.endswith("02-compose_down-paperless")
    assert added[6].unique_id.endswith("03-compose_start-paperless")
    assert added[7].unique_id.endswith("04-compose_stop-paperless")
    assert added[8].unique_id.endswith("05-compose_pull-paperless")
    assert added[19].unique_id.endswith("98-compose_image_prune")
    assert added[20].unique_id.endswith("99-compose_container_prune")
    assert added[4]._attr_suggested_object_id == "nas_01_compose_paperless_up"
    assert added[-5].unique_id.endswith("container_restart-ctr-db")
    assert added[-5]._attr_suggested_object_id == "nas_container_db_restart"
    assert added[-4].unique_id.endswith("vm_restart-vm-uuid-1234")
    assert added[-4]._attr_suggested_object_id == "nas_vm_homeassistant_restart"
    assert added[-3].unique_id.endswith("rsync_run-rsync-uuid-0001")
    assert added[-3]._attr_suggested_object_id == "nas_rsync_backup_media_run"
    assert added[-2].unique_id.endswith("rsync_run-rsync-uuid-0002")
    assert added[-1].unique_id.endswith("zfs_scrub-tank")
    assert added[-1]._attr_suggested_object_id == "nas_zfs_tank_scrub"


@pytest.mark.asyncio
async def test_async_setup_entry_omits_prune_buttons_without_docker_service(coordinator, config_entry) -> None:
    """Test global Docker prune buttons only appear when the service exists."""
    coordinator.data["service"] = [{"name": "ssh", "title": "SSH", "enabled": True, "running": True}]
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 27
    assert not any(entity.unique_id.endswith("98-compose_image_prune") for entity in added)
    assert not any(entity.unique_id.endswith("99-compose_container_prune") for entity in added)


@pytest.mark.asyncio
async def test_reboot_button_calls_reboot(coordinator) -> None:
    """Test reboot button RPC call."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVRebootButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "reboot")


@pytest.mark.asyncio
async def test_shutdown_button_calls_shutdown(coordinator) -> None:
    """Test shutdown button RPC call."""
    coordinator.api.async_call = AsyncMock()
    button = OMVShutdownButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "shutdown")


@pytest.mark.asyncio
async def test_compose_project_button_calls_do_command_and_refresh(coordinator) -> None:
    """Test compose project buttons trigger OMV compose commands."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVComposeProjectButton(
        coordinator,
        coordinator.data["compose_projects"][0],
        1,
        "compose_up",
        "up -d",
        "mdi:arrow-up-bold-box-outline",
    )

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doCommand",
        {"uuid": "proj-paperless", "command": "up -d"},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_system_button_calls_do_command_and_refresh(coordinator) -> None:
    """Test global compose maintenance buttons trigger OMV compose commands."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVComposeSystemButton(
        coordinator,
        98,
        "compose_image_prune",
        "image prune -f",
        "mdi:image-remove-outline",
    )

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doCommand",
        {"command": "image prune -f"},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_project_button_reads_background_output_when_present(coordinator) -> None:
    """Test compose project commands resolve OMV background output files."""

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Compose", "doCommand"):
            return {"filename": "compose-up.log"}
        if (service, method) == ("Exec", "getOutput"):
            return {"running": False, "output": "started"}
        raise AssertionError((service, method, params))

    coordinator.api.async_call = AsyncMock(side_effect=async_call)
    coordinator.async_request_refresh = AsyncMock()
    button = OMVComposeProjectButton(
        coordinator,
        coordinator.data["compose_projects"][0],
        1,
        "compose_up",
        "up -d",
        "mdi:arrow-up-bold-box-outline",
    )

    await button.async_press()

    assert coordinator.api.async_call.await_args_list[0].args == (
        "Compose",
        "doCommand",
        {"uuid": "proj-paperless", "command": "up -d"},
    )
    assert coordinator.api.async_call.await_args_list[1].args == (
        "Exec",
        "getOutput",
        {"filename": "compose-up.log", "pos": 0},
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_get_expected_button_unique_ids_includes_compose_project_actions(
    coordinator,
    config_entry,
) -> None:
    """Test cleanup state includes dynamically created compose project buttons."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-reboot" in unique_ids
    assert f"{config_entry.entry_id}-03-compose_start-paperless" in unique_ids
    assert f"{config_entry.entry_id}-05-compose_pull-web" in unique_ids
    assert f"{config_entry.entry_id}-98-compose_image_prune" in unique_ids
    assert f"{config_entry.entry_id}-99-compose_container_prune" in unique_ids


def test_get_expected_button_unique_ids_omits_prune_buttons_without_docker_service(
    coordinator,
    config_entry,
) -> None:
    """Test cleanup state drops prune button IDs when Docker is absent."""
    coordinator.data["service"] = [{"name": "ssh", "title": "SSH"}]

    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-98-compose_image_prune" not in unique_ids
    assert f"{config_entry.entry_id}-99-compose_container_prune" not in unique_ids


@pytest.mark.asyncio
async def test_apply_config_button_calls_api(coordinator) -> None:
    """Test apply_config button calls async_apply_config on the API."""
    coordinator.api.async_apply_config = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVApplyConfigButton(coordinator)

    await button.async_press()

    coordinator.api.async_apply_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_config_button_triggers_refresh(coordinator) -> None:
    """Test apply_config button triggers coordinator refresh before and after the call."""
    coordinator.api.async_apply_config = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    button = OMVApplyConfigButton(coordinator)

    await button.async_press()

    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_apply_config_button_raises_on_api_error(coordinator) -> None:
    """Test apply_config button raises HomeAssistantError when API call fails."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator.api.async_apply_config = AsyncMock(side_effect=Exception("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    button = OMVApplyConfigButton(coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "apply_config_failed"


@pytest.mark.asyncio
async def test_reboot_button_blocks_when_config_dirty(coordinator) -> None:
    """Test reboot button raises HomeAssistantError when configDirty is True."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator.data["hwinfo"]["configDirty"] = True
    coordinator.data["hwinfo"]["dirtyModules"] = ["nginx", "samba"]
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api.async_call = AsyncMock()
    button = OMVRebootButton(coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "reboot_blocked_config_dirty"
    coordinator.api.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_reboot_button_modules_in_placeholder(coordinator) -> None:
    """Test reboot button includes dirty module names in translation placeholders."""
    from homeassistant.exceptions import HomeAssistantError

    coordinator.data["hwinfo"]["configDirty"] = True
    coordinator.data["hwinfo"]["dirtyModules"] = ["nginx", "samba"]
    coordinator.async_request_refresh = AsyncMock()
    button = OMVRebootButton(coordinator)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_placeholders == {"modules": "nginx, samba"}


@pytest.mark.asyncio
async def test_reboot_button_proceeds_when_clean(coordinator) -> None:
    """Test reboot button calls System.reboot when config is clean."""
    coordinator.data["hwinfo"]["configDirty"] = False
    coordinator.data["hwinfo"]["dirtyModules"] = []
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api.async_call = AsyncMock()
    button = OMVRebootButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "reboot")


@pytest.mark.asyncio
async def test_container_restart_button_calls_do_container_command_and_refresh(coordinator) -> None:
    """Test container restart button triggers Compose.doContainerCommand and a refresh."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    container = next(c for c in coordinator.data["compose"] if c["container_key"] == "ctr-db")
    button = OMVContainerRestartButton(coordinator, container)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Compose",
        "doContainerCommand",
        {"id": "ctr-db", "command": "restart", "command2": ""},
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_get_expected_button_unique_ids_includes_apply_config(
    coordinator,
    config_entry,
) -> None:
    """Test get_expected_button_unique_ids includes the apply_config button."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-apply_config" in unique_ids


def test_get_expected_button_unique_ids_includes_vm_restart(
    coordinator,
    config_entry,
) -> None:
    """Test get_expected_button_unique_ids includes one restart button per VM."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-vm_restart-vm-uuid-1234" in unique_ids


@pytest.mark.asyncio
async def test_vm_restart_button_calls_kvm_do_command_and_refresh(coordinator) -> None:
    """Test the VM restart button issues Kvm.doCommand reboot and refreshes."""
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    vm = next(v for v in coordinator.data["kvm"] if v["vm_key"] == "vm-uuid-1234")
    button = OMVVmRestartButton(coordinator, vm)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Kvm",
        "doCommand",
        {
            "command": "reboot",
            "name": "homeassistant",
            "virttype": "vm",
            "vncport": "n/a",
            "spiceport": "n/a",
            "hostport": "n/a",
            "hostport2": "n/a",
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_vm_restart_button_raises_translated_error_on_api_failure(coordinator) -> None:
    """Test a failing Kvm.doCommand reboot raises a translated HomeAssistantError."""
    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    vm = next(v for v in coordinator.data["kvm"] if v["vm_key"] == "vm-uuid-1234")
    button = OMVVmRestartButton(coordinator, vm)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "vm_command_failed"
    assert exc_info.value.translation_placeholders == {
        "resource": "homeassistant",
        "command": "reboot",
    }
    coordinator.async_request_refresh.assert_awaited_once()


def test_get_expected_button_unique_ids_includes_rsync_run(
    coordinator,
    config_entry,
) -> None:
    """Test get_expected_button_unique_ids includes one run button per rsync job."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-rsync_run-rsync-uuid-0001" in unique_ids
    assert f"{config_entry.entry_id}-rsync_run-rsync-uuid-0002" in unique_ids


@pytest.mark.asyncio
async def test_rsync_run_button_calls_rsync_execute_and_refresh(coordinator) -> None:
    """Test the rsync run button fires Rsync.execute and refreshes."""
    coordinator.api.async_call = AsyncMock(return_value="/tmp/bgstatusXYZ")
    coordinator.async_request_refresh = AsyncMock()
    job = next(j for j in coordinator.data["rsync"] if j["rsync_key"] == "rsync-uuid-0001")
    button = OMVRsyncRunButton(coordinator, job)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Rsync",
        "execute",
        {"uuid": "rsync-uuid-0001"},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_rsync_run_button_raises_translated_error_on_api_failure(coordinator) -> None:
    """Test a failing Rsync.execute raises a translated HomeAssistantError."""
    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    job = next(j for j in coordinator.data["rsync"] if j["rsync_key"] == "rsync-uuid-0001")
    button = OMVRsyncRunButton(coordinator, job)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "rsync_execute_failed"
    assert exc_info.value.translation_placeholders == {"name": "Backup media"}
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_setup_entry_adds_no_cron_buttons_by_default(coordinator, config_entry) -> None:
    """Test no cron buttons are created when no jobs are selected (opt-in)."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any("cron_run" in entity.unique_id for entity in added)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_cron_buttons_for_selected_jobs(coordinator, config_entry) -> None:
    """Test cron buttons are created only for jobs selected in the options."""
    coordinator.hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_SELECTED_CRON_JOBS: ["cron-uuid-0001"]},
    )
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    cron_buttons = [entity for entity in added if "cron_run" in entity.unique_id]
    assert len(cron_buttons) == 1
    assert cron_buttons[0].unique_id.endswith("cron_run-cron-uuid-0001")
    assert cron_buttons[0]._attr_suggested_object_id == "nas_cron_nightly_cleanup_run"


def test_get_expected_button_unique_ids_respects_cron_opt_in(
    coordinator,
    config_entry,
) -> None:
    """Test expected cron button IDs follow the opt-in selection exactly."""
    default_ids = get_expected_button_unique_ids(config_entry, coordinator)
    assert not any("cron_run" in unique_id for unique_id in default_ids)

    coordinator.hass.config_entries.async_update_entry(
        config_entry,
        options={CONF_SELECTED_CRON_JOBS: ["cron-uuid-0001"]},
    )
    selected_ids = get_expected_button_unique_ids(config_entry, coordinator)
    assert f"{config_entry.entry_id}-cron_run-cron-uuid-0001" in selected_ids
    assert f"{config_entry.entry_id}-cron_run-cron-uuid-0002" not in selected_ids


@pytest.mark.asyncio
async def test_cron_run_button_calls_cron_execute_and_refresh(coordinator) -> None:
    """Test the cron run button fires Cron.execute and refreshes."""
    coordinator.api.async_call = AsyncMock(return_value="/tmp/bgstatusCRON")
    coordinator.async_request_refresh = AsyncMock()
    job = next(j for j in coordinator.data["cron"] if j["cron_key"] == "cron-uuid-0001")
    button = OMVCronRunButton(coordinator, job)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with(
        "Cron",
        "execute",
        {"uuid": "cron-uuid-0001"},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_cron_run_button_raises_translated_error_on_api_failure(coordinator) -> None:
    """Test a failing Cron.execute raises a translated HomeAssistantError."""
    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("RPC failed"))
    coordinator.async_request_refresh = AsyncMock()
    job = next(j for j in coordinator.data["cron"] if j["cron_key"] == "cron-uuid-0001")
    button = OMVCronRunButton(coordinator, job)

    with pytest.raises(HomeAssistantError) as exc_info:
        await button.async_press()

    assert exc_info.value.translation_key == "cron_execute_failed"
    assert exc_info.value.translation_placeholders == {"name": "Nightly cleanup"}
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_zfs_scrub_button_calls_scrub_and_refresh(coordinator) -> None:
    """Test the scrub button calls zfs.scrubPool with the plain pool name."""
    coordinator.api.async_call = AsyncMock(return_value=None)
    coordinator.async_request_refresh = AsyncMock()
    button = OMVZfsScrubButton(coordinator, coordinator.data["zfs"][0])

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("zfs", "scrubPool", {"name": "tank"})
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_zfs_scrub_button_raises_translated_error_on_api_failure(coordinator) -> None:
    """Test scrub failures surface as translated HomeAssistantError."""
    coordinator.async_scrub_zfs_pool = AsyncMock(side_effect=OMVApiError("boom"))
    coordinator.async_request_refresh = AsyncMock()
    button = OMVZfsScrubButton(coordinator, coordinator.data["zfs"][0])

    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()

    assert err.value.translation_key == "zfs_scrub_failed"
    assert err.value.translation_placeholders == {"name": "tank"}
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_expected_button_unique_ids_includes_zfs_scrub(coordinator, config_entry) -> None:
    """Test the registry cleanup whitelist covers scrub buttons per pool."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)

    assert f"{config_entry.entry_id}-zfs_scrub-tank" in unique_ids


@pytest.mark.asyncio
async def test_standby_button_calls_standby(coordinator) -> None:
    """Test standby button RPC call."""
    coordinator.api.async_call = AsyncMock()
    button = OMVStandbyButton(coordinator)

    assert button._attr_suggested_object_id == "nas_standby"
    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "standby")


@pytest.mark.asyncio
async def test_async_setup_entry_omits_wol_button_without_wol(coordinator, config_entry) -> None:
    """Test no WoL button is created when no interface has WoL enabled."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(isinstance(entity, OMVWolButton) for entity in added)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_wol_button_for_wol_interface(coordinator, config_entry) -> None:
    """Test a WoL button is created per WoL-capable interface with a MAC."""
    coordinator.data["network"][0]["wol"] = True
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    wol_buttons = [entity for entity in added if isinstance(entity, OMVWolButton)]
    assert len(wol_buttons) == 1
    assert wol_buttons[0].unique_id.endswith("wol-net-1")
    assert wol_buttons[0]._attr_suggested_object_id == "nas_wol_eth0"
    assert wol_buttons[0]._attr_translation_placeholders == {"interface": "eth0"}


@pytest.mark.asyncio
async def test_async_setup_entry_omits_wol_button_without_mac(coordinator, config_entry) -> None:
    """Test no WoL button when the interface has WoL but no MAC (virtual NIC)."""
    coordinator.data["network"][0]["wol"] = True
    coordinator.data["network"][0]["mac"] = ""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert not any(isinstance(entity, OMVWolButton) for entity in added)


@pytest.mark.asyncio
async def test_wol_button_sends_magic_packet(coordinator) -> None:
    """Test pressing the WoL button sends the magic packet with the fixture MAC."""
    coordinator.data["network"][0]["wol"] = True
    button = OMVWolButton(coordinator, coordinator.data["network"][0])

    with patch("custom_components.omv.button.async_send_magic_packet") as mock_send:
        await button.async_press()

    mock_send.assert_awaited_once_with(coordinator.hass, "aa:bb:cc:dd:ee:ff")


@pytest.mark.asyncio
async def test_wol_button_refreshes_mac_from_coordinator_data(coordinator) -> None:
    """Test the button uses the current coordinator MAC, not the cached one."""
    coordinator.data["network"][0]["wol"] = True
    button = OMVWolButton(coordinator, coordinator.data["network"][0])
    coordinator.data["network"][0]["mac"] = "11:22:33:44:55:66"

    with patch("custom_components.omv.button.async_send_magic_packet") as mock_send:
        await button.async_press()

    mock_send.assert_awaited_once_with(coordinator.hass, "11:22:33:44:55:66")


@pytest.mark.asyncio
async def test_wol_button_raises_translated_error_on_failure(coordinator) -> None:
    """Test WoL send failures surface as translated HomeAssistantError."""
    coordinator.data["network"][0]["wol"] = True
    button = OMVWolButton(coordinator, coordinator.data["network"][0])

    with (
        patch(
            "custom_components.omv.button.async_send_magic_packet",
            side_effect=OSError("network down"),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await button.async_press()

    assert err.value.translation_key == "wol_failed"
    assert err.value.translation_placeholders == {"interface": "eth0"}


@pytest.mark.asyncio
async def test_wol_button_available_when_coordinator_unreachable(coordinator) -> None:
    """WoL button must stay pressable even once other entities go unavailable (Issue #82)."""
    coordinator.data["network"][0]["wol"] = True
    button = OMVWolButton(coordinator, coordinator.data["network"][0])

    coordinator.last_update_success = False

    assert button.available is True


@pytest.mark.asyncio
async def test_standby_button_unavailable_when_coordinator_unreachable(coordinator) -> None:
    """Standby button follows normal availability — it needs a live connection (Issue #82)."""
    button = OMVStandbyButton(coordinator)

    coordinator.last_update_success = False

    assert button.available is False


def test_get_expected_button_unique_ids_includes_standby_and_wol(coordinator, config_entry) -> None:
    """Test the registry whitelist covers standby always and WoL conditionally."""
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)
    assert f"{config_entry.entry_id}-standby" in unique_ids
    assert f"{config_entry.entry_id}-wol-net-1" not in unique_ids

    coordinator.data["network"][0]["wol"] = True
    unique_ids = get_expected_button_unique_ids(config_entry, coordinator)
    assert f"{config_entry.entry_id}-wol-net-1" in unique_ids


@pytest.mark.asyncio
async def test_zfs_scrub_button_dedupes_across_disks(coordinator, config_entry) -> None:
    """A pool spanning multiple disks gets exactly one scrub button."""
    pool_a = coordinator.data["zfs"][0]
    pool_b = {**pool_a, "disk_key": "sdb"}  # same name "tank", different disk
    coordinator.data = {**coordinator.data, "zfs": [pool_a, pool_b]}

    added: list = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    unique_ids = [entity.unique_id for entity in added]
    assert len(unique_ids) == len(set(unique_ids))
    assert sum(uid.endswith("zfs_scrub-tank") for uid in unique_ids) == 1

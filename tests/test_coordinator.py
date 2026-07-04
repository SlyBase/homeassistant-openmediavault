"""Tests for the OMV data coordinator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.omv import _async_cleanup_stale_registry_entries
from custom_components.omv.const import (
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_VMS,
    CONF_SELECTED_ZFS_POOLS,
    CONF_SMART_INTERVAL,
    CONF_SMART_POLLING_DISABLED,
    DOMAIN,
)
from custom_components.omv.coordinator import OMVDataUpdateCoordinator
from custom_components.omv.exceptions import OMVApiError, OMVConnectionError


@pytest.mark.asyncio
async def test_coordinator_fetches_expected_data(hass, config_entry) -> None:
    """Test the coordinator normalizes the main OMV payloads."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Compose", "getVolumesBg"):
            return {"filename": "compose-volumes.json"}
        if (service, method) == ("Compose", "doContainerCommand"):
            return {"filename": f"inspect-{params['id']}.json"}
        if (service, method) == ("Exec", "getOutput"):
            outputs = {
                "compose-volumes.json": json.dumps(
                    {
                        "total": 1,
                        "data": [
                            {
                                "name": "paperless_data",
                                "size": 12300000000,
                                "mountpoint": "/var/lib/docker/volumes/paperless_data/_data",
                                "driver": "local",
                            }
                        ],
                    }
                ),
                "inspect-ctr-paperless-app.json": json.dumps(
                    [
                        {
                            "Mounts": [
                                {
                                    "Type": "volume",
                                    "Name": "paperless_data",
                                    "Destination": "/usr/src/paperless/data",
                                }
                            ],
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": "paperless",
                                    "com.docker.compose.service": "webserver",
                                    "org.opencontainers.image.version": "2.15.3",
                                }
                            },
                        }
                    ]
                ),
            }
            return {"running": False, "output": outputs[params["filename"]]}

        responses = {
            ("System", "getInformation"): {
                "hostname": "nas",
                "version": "8.1.2-1",
                "cpuModelName": "Intel(R) N100",
                "kernel": "Linux 6.6.0-omv",
                "cpuUtilization": 25.4,
                "memTotal": 100,
                "memUsed": 50,
                "uptime": 3600,
                "loadAverage": {"1min": 1.0, "5min": 0.5, "15min": 0.25},
                "rebootRequired": True,
                "availablePkgUpdates": 2,
            },
            ("CpuTemp", "get"): {"cputemp": 47.5},
            ("FileSystemMgmt", "enumerateFilesystems"): [
                {
                    "uuid": "fs-1",
                    "label": "data",
                    "type": "ext4",
                    "mounted": True,
                    "devicefile": "/dev/sda1",
                    "canonicaldevicefile": "/dev/sda1",
                    "parentdevicefile": "/dev/sda",
                    "available": 50 * 1073741824,
                    "size": 100 * 1073741824,
                    "percentage": 50,
                    "mountdir": "/srv/data",
                }
            ],
            ("Services", "getStatus"): [
                {"name": "ssh", "title": "SSH", "running": True, "enabled": True},
                {"name": "compose", "title": "Docker", "running": True, "enabled": True},
            ],
            ("Network", "enumerateDevices"): [
                {
                    "uuid": "net-1",
                    "devicename": "eth0",
                    "type": "ethernet",
                    "stats": {"rx_bytes": 1000, "tx_bytes": 500},
                }
            ],
            ("DiskMgmt", "enumerateDevices"): [
                {
                    "devicename": "sda",
                    "canonicaldevicefile": "/dev/sda",
                    "devicefile": "/dev/sda",
                    "model": "Disk",
                }
            ],
            ("Smart", "getListBg"): [{"devicename": "sda", "temperature": 32, "overallstatus": "PASSED"}],
            ("Smart", "getAttributes"): [{"attrname": "Raw_Read_Error_Rate", "rawvalue": "0 0 0"}],
            ("compose", "getContainerList"): {
                "data": [
                    {
                        "id": "ctr-paperless-app",
                        "name": "paperless-app",
                        "image": "ghcr.io/paperless-ngx/paperless-ngx:latest",
                        "state": "running",
                        "status": "Up 5 minutes",
                        "createdAt": "2026-03-13T10:00:00Z",
                        "startedAt": "2026-03-13T10:05:00Z",
                        "project": "paperless",
                        "service": "webserver",
                    }
                ]
            },
            ("compose", "getFileList"): {
                "data": [
                    {
                        "uuid": "proj-paperless",
                        "name": "paperless",
                        "status": "UP",
                        "uptime": "Up 5 minutes",
                        "svcname": "webserver",
                        "image": "ghcr.io/paperless-ngx/paperless-ngx:latest",
                    }
                ]
            },
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [{"name": "tank", "state": "ONLINE"}],
            ("Apt", "getUpgradedList"): {
                "total": 1,
                "data": [
                    {
                        "name": "docker-ce",
                        "version": "5:29.4.2-1~debian.12~bookworm",
                        "summary": "Docker: the open-source application container engine",
                        "maintainer": "Docker <support@docker.com>",
                        "homepage": "https://www.docker.com",
                        "repository": "Docker CE/bookworm",
                        "installedsize": 22735244,
                    }
                ],
            },
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["hwinfo"]["cpuModel"] == "Intel(R) N100"
    assert data["hwinfo"]["kernel"] == "Linux 6.6.0-omv"
    assert data["hwinfo"]["pkgUpdatesAvailable"] is True
    assert data["hwinfo"]["rebootRequired"] is True
    assert data["fs"][0]["disk_key"] == "sda"
    assert data["fs"][0]["free_percentage"] == 50.0
    assert data["disk"][0]["overallstatus"] == "PASSED"
    assert data["disk"][0]["raid_level"] == "unknown"
    assert data["disk"][0]["smart_details"]["temperature"] == 32
    assert data["disk"][0]["smart_attributes"]["Raw_Read_Error_Rate"] == "0"
    assert data["compose"][0]["container_key"] == "ctr-paperless-app"
    assert data["compose"][0]["project_key"] == "paperless"
    assert data["compose"][0]["version"] == "2.15.3"
    assert data["compose"][0]["status_detail"] == "Up 5 minutes"
    assert data["compose"][0]["project_status"] == "UP"
    assert data["compose_projects"][0]["container_total"] == 1
    assert data["compose_projects"][0]["uuid"] == "proj-paperless"
    assert data["compose_projects"][0]["status"] == "UP"
    assert data["compose_volumes"][0]["name"] == "paperless_data"
    assert data["compose_volumes"][0]["size_gb"] == 12.3
    assert data["zfs"][0]["name"] == "tank"
    assert any(
        call.args == ("compose", "getContainerList", {"start": 0, "limit": 999})
        for call in api.async_call.await_args_list
    )
    assert any(
        call.args == ("compose", "getFileList", {"start": 0, "limit": 999}) for call in api.async_call.await_args_list
    )
    assert any(
        call.args
        == (
            "Compose",
            "getVolumesBg",
            {"start": 0, "limit": -1, "sortdir": "asc", "sortfield": "name"},
        )
        for call in api.async_call.await_args_list
    )
    assert any(call.args[:2] == ("Compose", "doContainerCommand") for call in api.async_call.await_args_list)


@pytest.mark.asyncio
async def test_update_data_raises_reauth_on_two_factor_challenge(hass, config_entry) -> None:
    """A 2FA challenge surviving the API client's auto-reconnect must trigger reauth.

    Regression test: previously this fell into the "cached data" fallback and
    entities silently froze on stale values forever instead of prompting the
    user to log back in.
    """
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.omv.exceptions import OMVTwoFactorRequiredError

    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp"))
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_uses_mdmgmt_inventory_for_unmounted_md_arrays(hass, config_entry) -> None:
    """Test OMV 7 MdMgmt data creates md RAID devices even without filesystem entries."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {
                "hostname": "omv7",
                "version": "7.7.24-7",
                "cpuModelName": "Intel(R) Core(TM)",
                "kernel": "Linux 6.1.0-omv",
                "cpuUtilization": 12.5,
                "memTotal": 100,
                "memUsed": 25,
                "uptime": 3600,
                "loadAverage": {"1min": 0.1, "5min": 0.2, "15min": 0.3},
                "rebootRequired": False,
                "availablePkgUpdates": 0,
            },
            ("CpuTemp", "get"): {},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("MdMgmt", "enumerateDevices"): [
                {
                    "name": "omv7:0",
                    "devicefile": "/dev/md0",
                    "uuid": "74ab321f:98567b3e:db248598:80f5dd6a",
                    "level": "raid0",
                    "numdevices": 2,
                    "devices": ["/dev/loop10", "/dev/loop11"],
                    "size": "2143289344",
                    "state": "clean",
                    "description": "Software RAID omv7:0 [/dev/md0, raid0, 1.99 GiB]",
                }
            ],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): [],
            ("zfs", "listPools"): [],
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    await coordinator.async_init(await async_call("System", "getInformation"))
    data = await coordinator._async_update_data()

    md_disk = next(disk for disk in data["disk"] if disk["disk_key"] == "md0")
    raid = next(raid for raid in data["raid"] if raid["device"] == "md0")

    assert md_disk["israid"] is True
    assert md_disk["is_logical"] is True
    assert md_disk["raid_level"] == "raid0"
    assert md_disk["devicefile"] == "/dev/md0"
    assert md_disk["raid_member_disks"] == ["loop10", "loop11"]
    assert raid["health"] == "clean"
    assert raid["member_disks"] == ["loop10", "loop11"]


@pytest.mark.asyncio
async def test_container_version_prefers_metadata_over_image_tag(hass, config_entry) -> None:
    """Test labels, annotations and config labels beat the image tag fallback."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    assert (
        coordinator._extract_container_version(
            {
                "image": "vaultwarden/server:latest",
                "labels": {"org.opencontainers.image.version": "1.33.2"},
            }
        )
        == "1.33.2"
    )
    assert (
        coordinator._extract_container_version(
            {
                "image": "nginx:stable",
                "annotations": {"org.opencontainers.image.version": "1.27.4"},
            }
        )
        == "1.27.4"
    )
    assert (
        coordinator._extract_container_version(
            {
                "image": "custom/app:latest",
                "Config": {"Labels": {"org.opencontainers.image.version": "2026.3.0"}},
            }
        )
        == "2026.3.0"
    )
    # nginx / official images: version lives in ImageManifestDescriptor.annotations
    assert (
        coordinator._extract_container_version(
            {
                "image": "nginx:latest",
                "ImageManifestDescriptor": {
                    "annotations": {
                        "org.opencontainers.image.version": "1.29.6",
                    }
                },
            }
        )
        == "1.29.6"
    )


@pytest.mark.asyncio
async def test_parse_json_text_strips_shell_boilerplate(hass, config_entry) -> None:
    """OMV Exec.getOutput prepends a shell command header before the JSON payload.

    _parse_json_text must skip that header and still return valid parsed data.
    """
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    shell_prefix = (
        "export PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin; "
        "export LC_ALL=C.UTF-8; export LANGUAGE=; "
        "docker inspect abc123 2>&1\n"
    )
    inspect_payload = [
        {
            "Id": "abc123",
            "Config": {"Labels": {"org.opencontainers.image.version": "1.29.6"}},
            "ImageManifestDescriptor": {"annotations": {"org.opencontainers.image.version": "1.29.6"}},
        }
    ]
    raw = shell_prefix + json.dumps(inspect_payload)
    parsed = coordinator._parse_json_text(raw)
    assert parsed == inspect_payload

    # Also verify that version extraction works end-to-end once parsed
    inspect_data = parsed[0]
    version = coordinator._extract_container_version({"image": "nginx:latest", **inspect_data})
    assert version == "1.29.6"


@pytest.mark.asyncio
async def test_fetch_optional_background_json_with_shell_boilerplate(hass, config_entry) -> None:
    """Background inspect output with shell header still yields correct version.

    This is the exact pattern OMV7 returns for doContainerCommand inspect.
    """
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    nginx_inspect = [
        {
            "Id": "89aee99dfc2b",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "nginx",
                    "maintainer": "NGINX Docker Maintainers <docker-maint@nginx.com>",
                }
            },
            "ImageManifestDescriptor": {
                "annotations": {
                    "org.opencontainers.image.version": "1.29.6",
                }
            },
        }
    ]
    shell_header = (
        "export PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin; "
        "export LC_ALL=C.UTF-8; export LANGUAGE=; "
        "docker inspect 89aee99dfc2b 2>&1\n"
    )

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Compose", "doContainerCommand"):
            return "/tmp/bgstatusSOhiV3"
        if (service, method) == ("Exec", "getOutput"):
            return {"running": False, "output": shell_header + json.dumps(nginx_inspect)}
        raise AssertionError((service, method, params))

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    result = await coordinator._fetch_optional_background_json(
        "Compose",
        "doContainerCommand",
        {"id": "89aee99dfc2b", "command": "inspect", "command2": ""},
    )

    assert result == nginx_inspect
    inspect = coordinator._normalize_compose_inspect_response(result)
    assert inspect is not None
    version = coordinator._extract_container_version({"image": "nginx:latest", **inspect})
    assert version == "1.29.6"


@pytest.mark.asyncio
async def test_fetch_optional_background_json_handles_inline_output(hass, config_entry) -> None:
    """OMV may return command output inline instead of via a background task filename.

    If doContainerCommand returns {"output": "<json>", "running": false} directly,
    the JSON inside output must be parsed and returned so labels are accessible.
    """
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    inspect_inline = [
        {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "web",
                    "org.opencontainers.image.version": "1.35.4",
                }
            }
        }
    ]

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Compose", "doContainerCommand"):
            # Inline response instead of background-task filename
            return {"output": json.dumps(inspect_inline), "running": False}
        raise AssertionError((service, method, params))

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    result = await coordinator._fetch_optional_background_json(
        "Compose",
        "doContainerCommand",
        {"id": "ctr-nginx", "command": "inspect", "command2": ""},
    )

    assert result == inspect_inline
    inspect = coordinator._normalize_compose_inspect_response(result)
    assert inspect is not None
    assert isinstance(inspect.get("Config"), dict)
    version = coordinator._extract_container_version({"image": "nginx:latest", **inspect})
    assert version == "1.35.4"


@pytest.mark.asyncio
async def test_fetch_optional_background_json_handles_raw_json_string(hass, config_entry) -> None:
    """A raw JSON string returned directly must be parsed, not treated as a filename."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    inspect_data = [{"Config": {"Labels": {"org.opencontainers.image.version": "1.35.4"}}}]

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Compose", "doContainerCommand"):
            # Raw JSON string - not a filename
            return json.dumps(inspect_data)
        raise AssertionError((service, method, params))

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    result = await coordinator._fetch_optional_background_json(
        "Compose",
        "doContainerCommand",
        {"id": "ctr-nginx", "command": "inspect", "command2": ""},
    )

    assert result == inspect_data
    inspect = coordinator._normalize_compose_inspect_response(result)
    assert inspect is not None
    version = coordinator._extract_container_version({"image": "nginx:latest", **inspect})
    assert version == "1.35.4"


@pytest.mark.asyncio
async def test_compose_inspect_targets_includes_containers_without_project_key(hass, config_entry) -> None:
    """Containers with no project_key must still be inspect targets when selected_projects is set.

    filter_data_by_selection() keeps project_key='' containers through the
    projects filter (they are still shown in HA).  _compose_inspect_targets()
    must behave identically so those containers also receive inspect calls and
    get a proper version string.
    """

    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()

    options = {
        CONF_SELECTED_CONTAINERS: ["nginx"],
        CONF_SELECTED_COMPOSE_PROJECTS: ["paperless"],
    }
    patched_entry = config_entry.__class__(
        domain=config_entry.domain,
        title=config_entry.title,
        data=config_entry.data,
        options=options,
    )
    patched_entry.add_to_hass(hass)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        patched_entry,
        api,
        scan_interval=60,
    )

    compose = [
        # nginx: selected in selected_containers, but project_key is empty
        {"container_key": "nginx", "name": "nginx", "project_key": ""},
        # paperless-app: selected via project
        {"container_key": "ctr-paperless-app", "name": "paperless-app", "project_key": "paperless"},
        # other: not selected at all
        {"container_key": "other", "name": "other", "project_key": "other-project"},
    ]
    targets = coordinator._compose_inspect_targets(compose)
    target_keys = [c["container_key"] for c in targets]

    # nginx must be a target despite empty project_key (matches selected_containers AND
    # passes the project filter because project_key is empty)
    assert "nginx" in target_keys
    # paperless-app is in both selected_containers (no) but its project is selected
    # → actually NOT in selected_containers so filtered out at step 1
    assert "ctr-paperless-app" not in target_keys
    # other is in neither selection
    assert "other" not in target_keys


@pytest.mark.asyncio
async def test_compose_volume_normalization_skips_bind_mounts_and_parses_data_size(hass, config_entry) -> None:
    """Test bind mounts are ignored while real volumes keep parsed sizes."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    volumes = coordinator._normalize_compose_volumes(
        [],
        [
            {
                "container_key": "ctr-nginx",
                "name": "nginx",
                "project_key": "web",
                "project_name": "web",
                "image": "nginx:stable",
                "version": "1.27.4",
                "mounts": [
                    {
                        "Type": "bind",
                        "Source": "/srv/templates",
                        "Destination": "/etc/nginx/templates",
                    }
                ],
            },
            {
                "container_key": "ctr-vaultwarden",
                "name": "vaultwarden",
                "project_key": "vaultwarden",
                "project_name": "vaultwarden",
                "image": "vaultwarden/server:latest",
                "version": "1.33.2",
                "mounts": [
                    {
                        "Type": "volume",
                        "Name": "vaultwarden_data",
                        "Destination": "/data",
                        "Data": "4.8 GiB",
                    }
                ],
            },
        ],
    )

    assert [volume["volume_key"] for volume in volumes] == ["ctr-vaultwarden:vaultwarden_data"]
    assert volumes[0]["size_gb"] == 5.2
    assert volumes[0]["container_name"] == "vaultwarden"


@pytest.mark.asyncio
async def test_compose_volume_normalization_omv7_string_mounts(hass, config_entry) -> None:
    """OMV7 returns mounts as plain strings; volumes must be created and sized correctly."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    # OMV7 getContainerList: mounts is a plain string (named volume or bind path)
    compose = [
        {
            "container_key": "vaultwarden",
            "name": "vaultwarden",
            "project_key": "vaultwarden",
            "project_name": "vaultwarden",
            "image": "vaultwarden/server:latest",
            "version": "1.33.2",
            "mounts": "vaultwarden_data",  # OMV7: plain string
        },
        {
            "container_key": "nginx-web-1",
            "name": "nginx-web-1",
            "project_key": "web",
            "project_name": "web",
            "image": "nginx:stable",
            "version": "stable",
            "mounts": "/docker-data/nginx/templates",  # bind mount path — must be skipped
        },
    ]
    # Native volume records from getVolumesBg (raw bytes as returned by OMV7)
    native_volumes_response = {
        "total": 1,
        "data": [
            {
                "name": "vaultwarden_data",
                "size": 312115,
                "mountpoint": "/var/lib/docker/volumes/vaultwarden_data/_data",
                "driver": "local",
            }
        ],
    }

    volumes = coordinator._normalize_compose_volumes(native_volumes_response, compose)

    # The named volume must produce one entity; the bind-mount path must be dropped
    assert [v["volume_key"] for v in volumes] == ["vaultwarden:vaultwarden_data"]
    assert volumes[0]["container_name"] == "vaultwarden"
    assert volumes[0]["mountpoint"] == "/var/lib/docker/volumes/vaultwarden_data/_data"
    # 312115 bytes → must NOT be rounded to 0.0; expect a small positive value
    assert volumes[0]["size_gb"] is not None
    assert volumes[0]["size_gb"] > 0


@pytest.mark.asyncio
async def test_compose_volume_normalization_omv7_string_mounts_fallback(hass, config_entry) -> None:
    """When getVolumesBg returns nothing, the string-mount fallback still creates records."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    compose = [
        {
            "container_key": "vaultwarden",
            "name": "vaultwarden",
            "project_key": "vaultwarden",
            "project_name": "vaultwarden",
            "image": "vaultwarden/server:latest",
            "version": "1.33.2",
            "mounts": "vaultwarden_data",
        },
    ]

    # No native volumes (empty response → fallback path)
    volumes = coordinator._normalize_compose_volumes([], compose)

    assert [v["volume_key"] for v in volumes] == ["vaultwarden:vaultwarden_data"]
    assert volumes[0]["container_name"] == "vaultwarden"
    # No size data available in the fallback
    assert volumes[0]["size_gb"] is None


@pytest.mark.asyncio
async def test_fetch_optional_background_json_parses_exec_output(hass, config_entry) -> None:
    """Test OMV background helpers resolve Exec.getOutput JSON payloads."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Compose", "getVolumesBg"):
            return {"filename": "bg-volumes.json"}
        if (service, method) == ("Exec", "getOutput"):
            return {
                "running": False,
                "output": '{"total":1,"data":[{"name":"vaultwarden_data"}]}',
            }
        raise AssertionError((service, method, params))

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    response = await coordinator._fetch_optional_background_json(
        "Compose",
        "getVolumesBg",
        {"start": 0, "limit": -1},
    )

    assert response == {"total": 1, "data": [{"name": "vaultwarden_data"}]}


@pytest.mark.asyncio
async def test_coordinator_uses_legacy_smart_method_for_omv6(hass, config_entry) -> None:
    """Test OMV6 falls back to Smart.getList."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "6.9.0"},
            ("CpuTemp", "get"): {},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getList"): {"data": []},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)

    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    await coordinator.async_init({"hostname": "nas", "version": "6.9.0"})

    await coordinator._async_update_data()

    assert any(call.args[:2] == ("Smart", "getList") for call in api.async_call.await_args_list)


@pytest.mark.asyncio
async def test_coordinator_falls_back_when_smart_get_list_bg_returns_task_id(hass, config_entry) -> None:
    """Test OMV7+ falls back to Smart.getList when getListBg returns a task id."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [{"devicename": "sda", "canonicaldevicefile": "/dev/sda"}],
            ("Smart", "getListBg"): "task-123",
            ("Smart", "getList"): {"data": [{"devicename": "sda", "temperature": 32, "overallstatus": "GOOD"}]},
            ("Smart", "getAttributes"): [],
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)

    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["disk"][0]["overallstatus"] == "GOOD"
    assert any(call.args[:2] == ("Smart", "getListBg") for call in api.async_call.await_args_list)
    assert any(call.args == ("Smart", "getList", {"start": 0, "limit": 100}) for call in api.async_call.await_args_list)


@pytest.mark.asyncio
async def test_coordinator_exposes_unfiltered_inventory_but_filters_runtime_data(hass, config_entry) -> None:
    """Test live inventory stays unfiltered while runtime data honors saved selections."""
    config_entry = config_entry.__class__(
        domain=config_entry.domain,
        title=config_entry.title,
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
            CONF_SELECTED_SERVICES: ["ssh"],
            CONF_SELECTED_NETWORK_INTERFACES: ["net-1"],
            CONF_SELECTED_RAIDS: ["md0"],
            CONF_SELECTED_ZFS_POOLS: ["tank"],
            CONF_SELECTED_COMPOSE_PROJECTS: ["paperless"],
            CONF_SELECTED_CONTAINERS: ["ctr-paperless-app"],
        },
    )
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"
    api.async_call = AsyncMock()

    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    coordinator.data = coordinator.filter_data_by_selection(
        {
            "hwinfo": {},
            "disk": [
                {"disk_key": "sda", "devicename": "sda"},
                {"disk_key": "sdb", "devicename": "sdb"},
                {"disk_key": "md0", "devicename": "md0", "israid": True, "is_logical": True},
            ],
            "fs": [
                {"uuid": "fs-1", "disk_key": "sda"},
                {"uuid": "fs-2", "disk_key": "sdb"},
            ],
            "service": [{"name": "ssh"}, {"name": "smb"}],
            "network": [{"uuid": "net-1"}, {"uuid": "net-2"}],
            "raid": [{"device": "md0"}, {"device": "md1"}],
            "zfs": [{"name": "tank"}, {"name": "backup"}],
            "smart": [],
            "compose": [
                {
                    "container_key": "ctr-paperless-app",
                    "name": "paperless-app",
                    "project_key": "paperless",
                },
                {
                    "container_key": "ctr-web-nginx",
                    "name": "nginx",
                    "project_key": "web",
                },
            ],
            "compose_projects": [
                {"project_key": "paperless", "name": "paperless", "container_total": 1},
                {"project_key": "web", "name": "web", "container_total": 1},
            ],
            "compose_volumes": [
                {"volume_key": "ctr-paperless-app:data", "container_key": "ctr-paperless-app"},
                {"volume_key": "ctr-web-nginx:cache", "container_key": "ctr-web-nginx"},
            ],
            "kvm": [],
        },
        config_entry.options,
    )
    coordinator._inventory_source = {
        "disk": [
            {"disk_key": "sda", "devicename": "sda", "model": "Disk A"},
            {"disk_key": "sdb", "devicename": "sdb", "model": "Disk B"},
            {"disk_key": "md0", "devicename": "md0", "model": "Linux MD RAID", "israid": True},
        ],
        "fs": [
            {"uuid": "fs-1", "label": "data"},
            {"uuid": "fs-2", "label": "backup"},
        ],
        "service": [{"name": "ssh", "title": "SSH"}, {"name": "smb", "title": "SMB"}],
        "network": [{"uuid": "net-1", "devicename": "eth0"}, {"uuid": "net-2", "devicename": "eth1"}],
        "raid": [{"device": "md0"}, {"device": "md1"}],
        "zfs": [{"name": "tank"}, {"name": "backup"}],
        "compose": [
            {
                "container_key": "ctr-paperless-app",
                "name": "paperless-app",
                "image": "ghcr.io/paperless-ngx/paperless-ngx:latest",
                "project_key": "paperless",
                "project_name": "paperless",
            },
            {
                "container_key": "ctr-web-nginx",
                "name": "nginx",
                "image": "nginx:stable",
                "project_key": "web",
                "project_name": "web",
            },
        ],
        "compose_projects": [
            {"project_key": "paperless", "name": "paperless", "container_total": 1},
            {"project_key": "web", "name": "web", "container_total": 1},
        ],
        "compose_volumes": [
            {"volume_key": "ctr-paperless-app:data", "container_key": "ctr-paperless-app"},
            {"volume_key": "ctr-web-nginx:cache", "container_key": "ctr-web-nginx"},
        ],
    }

    inventory = coordinator.get_live_inventory()

    assert [item["value"] for item in inventory[CONF_SELECTED_DISKS]] == ["md0", "sda", "sdb"]
    assert [disk["disk_key"] for disk in coordinator.data["disk"]] == ["sda", "md0"]
    assert [filesystem["uuid"] for filesystem in coordinator.data["fs"]] == ["fs-1"]
    assert [item["value"] for item in inventory[CONF_SELECTED_CONTAINERS]] == [
        "ctr-paperless-app",
        "ctr-web-nginx",
    ]
    assert [container["container_key"] for container in coordinator.data["compose"]] == ["ctr-paperless-app"]
    assert [project["project_key"] for project in coordinator.data["compose_projects"]] == ["paperless"]
    assert [volume["volume_key"] for volume in coordinator.data["compose_volumes"]] == ["ctr-paperless-app:data"]


@pytest.mark.asyncio
async def test_coordinator_treats_empty_raid_selection_as_unfiltered_for_md_devices(hass, config_entry) -> None:
    """Test new md RAID resources are not hidden by an explicit empty selected_raids list."""
    config_entry = config_entry.__class__(
        domain=config_entry.domain,
        title=config_entry.title,
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_RAIDS: [],
        },
    )
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"
    api.async_call = AsyncMock()

    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    filtered = coordinator.filter_data_by_selection(
        {
            "hwinfo": {},
            "disk": [
                {"disk_key": "sda", "devicename": "sda"},
                {"disk_key": "md0", "devicename": "md0", "israid": True, "is_logical": True},
            ],
            "fs": [],
            "service": [],
            "network": [],
            "raid": [{"device": "md0", "disk_key": "md0", "health": "clean"}],
            "zfs": [],
            "smart": [],
            "compose": [],
            "compose_projects": [],
            "compose_volumes": [],
            "kvm": [],
            "gpu": {},
        },
        config_entry.options,
    )

    assert [disk["disk_key"] for disk in filtered["disk"]] == ["sda", "md0"]
    assert [raid["device"] for raid in filtered["raid"]] == ["md0"]


@pytest.mark.asyncio
async def test_filesystem_mapping_uses_parent_and_canonical_device_files(hass, config_entry) -> None:
    """Test filesystem to disk mapping uses path hints and ZFS size fallback."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    disks = [
        {
            "disk_key": "sda",
            "devicename": "sda",
            "devicefile": "/dev/sda",
            "canonicaldevicefile": "/dev/sda",
        },
        {
            "disk_key": "nvme0n1",
            "devicename": "nvme0n1",
            "devicefile": "/dev/nvme0n1",
            "canonicaldevicefile": "/dev/nvme0n1",
        },
        {
            "disk_key": "sdc",
            "devicename": "sdc",
            "devicefile": "/dev/sdc",
            "canonicaldevicefile": "/dev/sdc",
            "total_size_gb": 2000.4,
        },
    ]

    filesystems = coordinator._normalize_filesystems(
        [
            {
                "uuid": "fs-1",
                "type": "ext4",
                "devicefile": "/dev/sda1",
                "canonicaldevicefile": "/dev/sda1",
                "parentdevicefile": "/dev/sda",
                "available": 10 * 1073741824,
                "size": 20 * 1073741824,
                "percentage": 50,
            },
            {
                "uuid": "fs-2",
                "type": "ext4",
                "devicefile": "/dev/nvme0n1p2",
                "canonicaldevicefile": "/dev/nvme0n1p2",
                "available": 15 * 1073741824,
                "size": 30 * 1073741824,
                "percentage": 50,
            },
            {
                "type": "zfs",
                "devicename": "BigData",
                "devicefile": "BigData",
                "canonicaldevicefile": "BigData",
                "label": "BigData",
                "mounted": True,
                "available": 1660262557941.76,
                "size": 1930845497589.76,
                "percentage": 14,
                "mountpoint": "/BigData",
            },
        ],
        disks,
    )

    assert filesystems[0]["disk_key"] == "sda"
    assert filesystems[1]["disk_key"] == "nvme0n1"
    assert filesystems[2]["disk_key"] == "sdc"
    assert filesystems[2]["mountdir"] == "/BigData"


@pytest.mark.asyncio
async def test_coordinator_maps_omv8_style_zfs_pool_to_disk(hass, config_entry) -> None:
    """Test OMV8 ZFS pools attach to the matching disk device instead of the hub."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {},
            ("FileSystemMgmt", "enumerateFilesystems"): [
                {
                    "type": "zfs",
                    "devicename": "BigData",
                    "devicefile": "BigData",
                    "canonicaldevicefile": "BigData",
                    "label": "BigData",
                    "mounted": True,
                    "available": 1660262557941.76,
                    "size": 1930845497589.76,
                    "percentage": 14,
                    "mountpoint": "/BigData",
                }
            ],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [
                {
                    "devicename": "sdc",
                    "canonicaldevicefile": "/dev/sdc",
                    "devicefile": "/dev/sdc",
                    "model": "QEMU HARDDISK",
                    "size": "2000398934016",
                },
                {
                    "devicename": "sdd",
                    "canonicaldevicefile": "/dev/sdd",
                    "devicefile": "/dev/sdd",
                    "model": "QEMU HARDDISK",
                    "size": "1000204886016",
                },
            ],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("Smart", "getAttributes"): {"data": []},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): {
                "data": [
                    {
                        "available": 1660262557941.76,
                        "expanded": True,
                        "icon": "images/raid.png",
                        "id": "root/pool-BigData",
                        "mountpoint": "/BigData",
                        "name": "BigData",
                        "origin": "n/a",
                        "path": "BigData",
                        "size": 1930845497589.76,
                        "state": "ONLINE",
                        "status": "OK",
                        "type": "Pool",
                        "used": 270582939648,
                        "usedpercent": 14.01370228668031,
                    }
                ],
                "total": 1,
            },
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["fs"][0]["disk_key"] == "sdc"
    assert data["zfs"][0]["disk_key"] == "sdc"


@pytest.mark.asyncio
async def test_coordinator_creates_synthetic_md_devices_and_maps_zfs(hass, config_entry) -> None:
    """Test md arrays are synthesized from filesystems and reused by RAID/ZFS mapping."""
    config_entry.add_to_hass(hass)
    api = Mock()

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {},
            ("FileSystemMgmt", "enumerateFilesystems"): [
                {
                    "uuid": "fs-md127",
                    "label": "bigdata",
                    "type": "ext4",
                    "mounted": True,
                    "devicefile": "/dev/md127",
                    "canonicaldevicefile": "/dev/md127",
                    "parentdevicefile": "/dev/md127",
                    "available": 100 * 1000000000,
                    "size": 200 * 1000000000,
                    "percentage": 50,
                    "mountdir": "/srv/bigdata",
                }
            ],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [
                {"devicename": "sdd", "canonicaldevicefile": "/dev/sdd", "devicefile": "/dev/sdd"},
                {"devicename": "sde", "canonicaldevicefile": "/dev/sde", "devicefile": "/dev/sde"},
            ],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("Smart", "getAttributes"): {"data": []},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [
                {
                    "name": "bigdata",
                    "state": "ONLINE",
                    "mountpoint": "/srv/bigdata",
                    "size": "200 GB",
                    "alloc": "100 GB",
                    "free": "100 GB",
                    "capacity": 50,
                }
            ],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    md_disk = next(disk for disk in data["disk"] if disk["disk_key"] == "md127")
    assert md_disk["israid"] is True
    assert md_disk["storage_source"] == "zfs"
    assert md_disk["used_size_gb"] == 100.0
    assert md_disk["free_size_gb"] == 100.0
    assert data["fs"][0]["disk_key"] == "md127"
    assert data["raid"][0]["device"] == "md127"
    assert data["raid"][0]["health"] == "clean"
    assert data["zfs"][0]["disk_key"] == "md127"


@pytest.mark.asyncio
async def test_zfs_pool_mapping_accepts_child_mountpoints(hass, config_entry) -> None:
    """Test ZFS pools can map to a disk through child filesystem mountpoints."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    disk_key = coordinator._map_zfs_pool_to_disk(
        "/tank",
        "tank",
        [
            {
                "mountdir": "/tank/media",
                "label": "media",
                "disk_key": "sdc",
            }
        ],
        [],
        {},
    )

    assert disk_key == "sdc"


@pytest.mark.asyncio
async def test_zfs_pool_mapping_uses_origin_or_id_device_references(hass, config_entry) -> None:
    """Test ZFS pools can map directly via origin/id device references."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    disk_key = coordinator._map_zfs_pool_to_disk(
        "/tank",
        "tank",
        [],
        [
            {
                "disk_key": "sdc",
                "devicename": "sdc",
                "devicefile": "/dev/sdc",
                "canonicaldevicefile": "/dev/sdc",
                "total_size_gb": 2000.4,
            }
        ],
        {"id": "/dev/sdc", "size": "2000398934016"},
    )

    assert disk_key == "sdc"


@pytest.mark.asyncio
async def test_numeric_string_sizes_are_treated_as_bytes(hass, config_entry) -> None:
    """Test raw numeric size strings are interpreted as bytes instead of GB."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    assert coordinator._coerce_storage_gb("2000398934016") == 2000.4


@pytest.mark.asyncio
async def test_container_version_falls_back_to_image_tag(hass, config_entry) -> None:
    """Test container version falls back to the image tag when labels are unavailable."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    assert coordinator._extract_container_version({"image": "vaultwarden/server:1.33.2"}) == "1.33.2"


@pytest.mark.asyncio
async def test_cleanup_removes_deselected_entities_and_child_devices(hass, coordinator, config_entry) -> None:
    """Test stale registry entries are removed when resources are deselected."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    stale_device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{config_entry.entry_id}:disk:sdx")},
        name="Disk sdx",
    )
    entity_registry.async_get_or_create(
        DOMAIN,
        "sensor",
        f"{config_entry.entry_id}-disk_used_size-sdx",
        config_entry=config_entry,
        device_id=stale_device.id,
        original_name="sdx Used Size",
    )
    entity_registry.async_get_or_create(
        DOMAIN,
        "binary_sensor",
        f"{config_entry.entry_id}-service-smb",
        config_entry=config_entry,
        original_name="SMB Service",
    )
    stale_project = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{config_entry.entry_id}:compose_project:legacy")},
        name="Compose legacy",
    )
    stale_container = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{config_entry.entry_id}:container:legacy-app")},
        via_device=(DOMAIN, f"{config_entry.entry_id}:compose_project:legacy"),
        name="legacy-app",
    )
    entity_registry.async_get_or_create(
        DOMAIN,
        "sensor",
        f"{config_entry.entry_id}-compose_project_total-legacy",
        config_entry=config_entry,
        device_id=stale_project.id,
        original_name="legacy containers total",
    )
    entity_registry.async_get_or_create(
        DOMAIN,
        "sensor",
        f"{config_entry.entry_id}-container_state-legacy-app",
        config_entry=config_entry,
        device_id=stale_container.id,
        original_name="legacy-app state",
    )
    entity_registry.async_get_or_create(
        DOMAIN,
        "button",
        f"{config_entry.entry_id}-compose_up-paperless",
        config_entry=config_entry,
        original_name="legacy paperless up",
    )
    entity_registry.async_get_or_create(
        DOMAIN,
        "button",
        f"{config_entry.entry_id}-98-compose_image_prune",
        config_entry=config_entry,
        original_name="docker image prune",
    )

    coordinator.data["service"] = [{"name": "ssh", "title": "SSH", "running": True}]

    await _async_cleanup_stale_registry_entries(hass, config_entry, coordinator)

    assert (
        entity_registry.async_get_entity_id(
            DOMAIN,
            "sensor",
            f"{config_entry.entry_id}-disk_used_size-sdx",
        )
        is None
    )
    assert (
        entity_registry.async_get_entity_id(
            DOMAIN,
            "binary_sensor",
            f"{config_entry.entry_id}-service-smb",
        )
        is None
    )
    assert device_registry.async_get_device({(DOMAIN, f"{config_entry.entry_id}:disk:sdx")}, set()) is None
    assert (
        entity_registry.async_get_entity_id(
            "button",
            DOMAIN,
            f"{config_entry.entry_id}-compose_up-paperless",
        )
        is None
    )
    assert (
        entity_registry.async_get_entity_id(
            "button",
            DOMAIN,
            f"{config_entry.entry_id}-98-compose_image_prune",
        )
        is None
    )
    assert (
        device_registry.async_get_device(
            {(DOMAIN, f"{config_entry.entry_id}:compose_project:legacy")},
            set(),
        )
        is None
    )
    assert (
        device_registry.async_get_device(
            {(DOMAIN, f"{config_entry.entry_id}:container:legacy-app")},
            set(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_cpu_temp_zero_is_filtered_to_none(hass, config_entry) -> None:
    """Test that a CPU temperature of 0°C (reported by VMs) is treated as no-data."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["hwinfo"]["cputemp"] is None
    assert any(call.args[:2] == ("CpuTemp", "get") for call in api.async_call.await_args_list)


@pytest.mark.asyncio
async def test_network_rates_are_calculated_from_previous_counters(hass, config_entry) -> None:
    """Test network rates use deltas between refreshes."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    responses = [
        [
            {
                "uuid": "net-1",
                "devicename": "eth0",
                "type": "ethernet",
                "stats": {"rx_bytes": 1000, "tx_bytes": 500},
            }
        ],
        [
            {
                "uuid": "net-1",
                "devicename": "eth0",
                "type": "ethernet",
                "stats": {"rx_bytes": 1600, "tx_bytes": 1100},
            }
        ],
    ]

    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    first = coordinator._normalize_network(responses[0])
    second = coordinator._normalize_network(responses[1])

    assert first[0]["rx"] == 0.0
    assert second[0]["rx"] == 0.0
    assert second[0]["tx"] == 0.0


@pytest.mark.asyncio
async def test_normalize_network_maps_ether_to_lowercase_mac(hass, config_entry) -> None:
    """Test the raw `ether` field becomes a lowercased `mac` field."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    records = coordinator._normalize_network(
        [
            {
                "uuid": "net-1",
                "devicename": "eth0",
                "type": "ethernet",
                "ether": "AA:BB:CC:DD:EE:FF",
                "wol": True,
            },
            {"uuid": "net-2", "devicename": "veth0", "type": "ethernet"},
        ]
    )

    assert records[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert records[0]["wol"] is True
    assert records[1]["mac"] == ""


@pytest.mark.asyncio
async def test_smart_skips_getattributes_for_hotpluggable_disk(hass, config_entry) -> None:
    """SMART getAttributes must not be called for hotpluggable (USB) disks."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock(return_value=[])
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    # Simulate a USB stick: hotpluggable=True, has a canonical device file
    disks = [
        {
            "disk_key": "sdb",
            "devicename": "sdb",
            "canonicaldevicefile": "/dev/sdb",
            "devicefile": "/dev/sdb",
            "hotpluggable": True,
            "overallstatus": "unknown",
        }
    ]
    # Smart.getList returns one matching SMART record
    api.async_call = AsyncMock(
        return_value={"data": [{"devicename": "sdb", "temperature": 30, "overallstatus": "PASSED"}]}
    )
    coordinator.omv_version = 7

    await coordinator._async_fetch_smart(disks)

    # getAttributes must NOT have been called for the hotpluggable disk
    for call in api.async_call.await_args_list:
        assert not (len(call.args) >= 2 and call.args[0] == "Smart" and call.args[1] == "getAttributes"), (
            "getAttributes was called for a hotpluggable disk"
        )


@pytest.mark.asyncio
async def test_smart_does_not_skip_getattributes_for_non_hotpluggable_disk(hass, config_entry) -> None:
    """SMART getAttributes must still be called for regular (non-hotpluggable) disks."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    disks = [
        {
            "disk_key": "sda",
            "devicename": "sda",
            "canonicaldevicefile": "/dev/sda",
            "devicefile": "/dev/sda",
            "hotpluggable": False,
            "overallstatus": "unknown",
        }
    ]

    async def async_call(service, method, params=None, **kwargs):
        if method == "getListBg":
            return {"data": [{"devicename": "sda", "temperature": 35, "overallstatus": "PASSED"}]}
        if method == "getAttributes":
            return {"data": [{"attrname": "Raw_Read_Error_Rate", "rawvalue": "0"}]}
        return []

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator.omv_version = 8

    await coordinator._async_fetch_smart(disks)

    assert any(
        len(call.args) >= 2 and call.args[0] == "Smart" and call.args[1] == "getAttributes"
        for call in api.async_call.await_args_list
    ), "getAttributes was not called for a regular disk"


@pytest.mark.asyncio
async def test_smart_skips_getattributes_after_failure(hass, config_entry) -> None:
    """SMART getAttributes must be skipped on subsequent polls after an HTTP 500 failure."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )
    disks = [
        {
            "disk_key": "sda",
            "devicename": "sda",
            "canonicaldevicefile": "/dev/sda",
            "devicefile": "/dev/sda",
            "hotpluggable": False,
            "overallstatus": "unknown",
        }
    ]

    async def async_call_fail(service, method, params=None, **kwargs):
        if method in ("getListBg", "getList"):
            return {"data": [{"devicename": "sda", "temperature": 35, "overallstatus": "PASSED"}]}
        if method == "getAttributes":
            raise OMVConnectionError("OMV returned HTTP 500")
        return []

    api.async_call = AsyncMock(side_effect=async_call_fail)
    coordinator.omv_version = 8

    # First poll: getAttributes fails, device added to _smart_no_attributes
    await coordinator._async_fetch_smart(disks)
    assert "/dev/sda" in coordinator._smart_no_attributes

    api.async_call.reset_mock()

    # Second poll: getAttributes must NOT be called again
    await coordinator._async_fetch_smart(disks)
    for call in api.async_call.await_args_list:
        assert not (len(call.args) >= 2 and call.args[0] == "Smart" and call.args[1] == "getAttributes"), (
            "getAttributes was called again after a permanent failure"
        )


@pytest.mark.asyncio
async def test_smart_polling_disabled_skips_all_rpcs(hass, config_entry) -> None:
    """With SMART polling disabled no Smart RPC runs and disks keep no SMART data (#41)."""
    patched_entry = config_entry.__class__(
        domain=config_entry.domain,
        title=config_entry.title,
        data=config_entry.data,
        options={CONF_SMART_POLLING_DISABLED: True},
    )
    patched_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock(return_value={"data": []})
    coordinator = OMVDataUpdateCoordinator(hass, patched_entry, api, scan_interval=60)
    coordinator.omv_version = 8

    disks = [
        {
            "disk_key": "sda",
            "devicename": "sda",
            "canonicaldevicefile": "/dev/sda",
            "devicefile": "/dev/sda",
            "hotpluggable": False,
            "overallstatus": "unknown",
        }
    ]

    records = await coordinator._async_collect_smart(disks)

    assert records == []
    assert "temperature" not in disks[0]
    for call in api.async_call.await_args_list:
        assert call.args[0] != "Smart", "no SMART RPC should run when polling is disabled"


@pytest.mark.asyncio
async def test_smart_interval_caches_between_polls(hass, config_entry) -> None:
    """SMART RPCs run once per interval; cached data is re-applied to disks in between (#41)."""
    patched_entry = config_entry.__class__(
        domain=config_entry.domain,
        title=config_entry.title,
        data=config_entry.data,
        options={CONF_SMART_INTERVAL: 3600},
    )
    patched_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        if method == "getListBg":
            return {"data": [{"devicename": "sda", "temperature": 35, "overallstatus": "PASSED"}]}
        if method == "getAttributes":
            return {"data": [{"attrname": "Raw_Read_Error_Rate", "rawvalue": "0"}]}
        return []

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, patched_entry, api, scan_interval=60)
    coordinator.omv_version = 8

    def make_disks() -> list[dict]:
        return [
            {
                "disk_key": "sda",
                "devicename": "sda",
                "canonicaldevicefile": "/dev/sda",
                "devicefile": "/dev/sda",
                "hotpluggable": False,
                "overallstatus": "unknown",
            }
        ]

    # First poll: live fetch populates the cache and the disk.
    disks1 = make_disks()
    await coordinator._async_collect_smart(disks1)
    smart_calls_first = sum(1 for c in api.async_call.await_args_list if c.args[0] == "Smart")
    assert smart_calls_first > 0
    assert disks1[0]["temperature"] == 35

    api.async_call.reset_mock()

    # Second poll within the interval: no SMART RPC, cache still applied to fresh disks.
    disks2 = make_disks()
    await coordinator._async_collect_smart(disks2)
    smart_calls_second = sum(1 for c in api.async_call.await_args_list if c.args[0] == "Smart")
    assert smart_calls_second == 0
    assert disks2[0]["temperature"] == 35
    assert disks2[0]["smart_attributes"] == {"Raw_Read_Error_Rate": "0"}


@pytest.mark.asyncio
async def test_normalize_hwinfo_uses_api_memused_field(hass, config_entry) -> None:
    """memUsed must use the API's memUsed field (= total - available), not total - free.

    On systems with aggressive kernel caching (e.g. Raspberry Pi), memFree is
    tiny while memAvailable is large. Using total-free would give ~93% instead
    of the correct ~28% that the OMV GUI itself shows.
    """
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    # memFree is tiny (lots of kernel cache), but memUsed (= total - available) is small
    result = coordinator._normalize_hwinfo(
        {
            "hostname": "nas",
            "version": "8.1.2-1",
            "cpuUtilization": 10.0,
            "memTotal": 16000,
            "memFree": 500,  # tiny free → total-free would be 96.9%
            "memUsed": 4480,  # API's memUsed = total - available (= 28%)
            "uptime": 0,
            "availablePkgUpdates": 0,
        }
    )
    # Must use API's memUsed (4480), not total-free (15500)
    assert result["memUsed"] == 4480
    assert result["memUsage"] == 28.0


@pytest.mark.asyncio
async def test_normalize_hwinfo_falls_back_to_calculated_memusage(hass, config_entry) -> None:
    """memUsage must fall back to memUsed/memTotal when memUtilization is absent."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    result = coordinator._normalize_hwinfo(
        {
            "hostname": "nas",
            "version": "7.0.0-1",
            "cpuUtilization": 5.0,
            "memTotal": 8000,
            "memUsed": 2000,
            # memUtilization intentionally absent
            "uptime": 0,
            "availablePkgUpdates": 0,
        }
    )
    # 2000/8000 * 100 = 25.0
    assert result["memUsage"] == 25.0


@pytest.mark.asyncio
async def test_normalize_disks_stores_hotpluggable_flag(hass, config_entry) -> None:
    """_normalize_disks must store the hotpluggable flag from the API response."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    disks = coordinator._normalize_disks(
        [
            {
                "devicename": "sda",
                "canonicaldevicefile": "/dev/sda",
                "devicefile": "/dev/sda",
                "hotpluggable": True,
            },
            {
                "devicename": "sdb",
                "canonicaldevicefile": "/dev/sdb",
                "devicefile": "/dev/sdb",
                "hotpluggable": False,
            },
        ]
    )

    assert disks[0]["hotpluggable"] is True
    assert disks[1]["hotpluggable"] is False


@pytest.mark.asyncio
async def test_virtual_filesystems_skip_size_based_disk_mapping(hass, config_entry) -> None:
    """Virtual/pooling filesystems must not be matched to a disk via size fallback."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
    )

    disks = [
        {
            "disk_key": "sdb",
            "devicename": "sdb",
            "devicefile": "/dev/sdb",
            "canonicaldevicefile": "/dev/sdb",
            "total_size_gb": 119.2,
        },
    ]

    filesystems = coordinator._normalize_filesystems(
        [
            {
                "uuid": "f6ec31b6-a888-4ba8-84dc-b40e73fbfcc7",
                "type": "fuse.mergerfs",
                "label": "mergerfs_test",
                "devicename": "/srv/mergerfs/mergerfs_test",
                "devicefile": "/srv/mergerfs/mergerfs_test",
                "canonicaldevicefile": "/srv/mergerfs/mergerfs_test",
                "parentdevicefile": False,
                "mounted": True,
                "mountpoint": "/srv/mergerfs/mergerfs_test",
                "available": 117 * 1000000000,
                "size": 117 * 1000000000,
                "percentage": 1,
            },
            {
                "uuid": "nfs-uuid-789",
                "type": "nfs4",
                "label": "nas-share",
                "devicename": "192.168.1.100:/share",
                "devicefile": "192.168.1.100:/share",
                "canonicaldevicefile": "192.168.1.100:/share",
                "parentdevicefile": False,
                "mounted": True,
                "mountpoint": "/srv/nfs",
                "available": 100 * 1000000000,
                "size": 119 * 1000000000,
                "percentage": 16,
            },
        ],
        disks,
    )

    assert filesystems[0]["disk_key"] is None, "mergerfs must not map to a disk via size"
    assert filesystems[1]["disk_key"] is None, "nfs must not map to a disk via size"


@pytest.mark.asyncio
async def test_normalize_disks_strips_dev_prefix_from_devicename(hass, config_entry) -> None:
    """_normalize_disks must normalize devicename by stripping /dev/ prefix.

    OMV 8 may return devicename as '/dev/md0' instead of 'md0'. Without
    normalization, _augment_disks_with_logical_storage would later add a
    second synthetic 'md0' entry (since 'md0' != '/dev/md0'), producing
    duplicate sensors for every md RAID array (Issue #27).
    """
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    disks = coordinator._normalize_disks(
        [
            {
                "devicename": "/dev/md0",
                "canonicaldevicefile": "/dev/md0",
                "devicefile": "/dev/md0",
                "israid": True,
                "description": "RAID 1 (md0)",
            },
        ]
    )

    assert len(disks) == 1
    assert disks[0]["disk_key"] == "md0", "disk_key must not contain /dev/ prefix"
    assert disks[0]["devicename"] == "md0", "devicename must not contain /dev/ prefix"


@pytest.mark.asyncio
async def test_augment_disks_does_not_add_duplicate_when_dev_prefix_present(hass, config_entry) -> None:
    """_augment_disks_with_logical_storage must not add a synthetic entry when
    the disk list already contains the md device (even if its disk_key has
    the /dev/ prefix from an older normalization path).

    Regression test for Issue #27: duplicate disk sensors for md RAID arrays.
    """
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    # Simulate a disk list that has the md device with /dev/ prefix still present
    # (guards against regressions if a future code path re-introduces unnormalized keys)
    existing_disks = [
        {
            "disk_key": "/dev/md0",
            "devicename": "/dev/md0",
            "canonicaldevicefile": "/dev/md0",
            "devicefile": "/dev/md0",
            "israid": True,
            "is_logical": False,
        }
    ]

    # Filesystem record that points to md0 via canonicaldevicefile
    filesystem_response = [
        {
            "uuid": "fs-uuid-1",
            "devicename": "md0",
            "devicefile": "/dev/md0",
            "canonicaldevicefile": "/dev/md0",
            "parentdevicefile": "/dev/md0",
            "type": "ext4",
            "size": "2000000000",
        }
    ]

    result = coordinator._augment_disks_with_logical_storage(existing_disks, filesystem_response)

    md_entries = [d for d in result if "md0" in str(d.get("disk_key", ""))]
    assert len(md_entries) == 1, (
        f"Expected 1 md0 disk entry, got {len(md_entries)}: {[d['disk_key'] for d in md_entries]}"
    )


@pytest.mark.asyncio
async def test_tempmon_sensors_normalized(hass, config_entry) -> None:
    """Test that TempMon.getSensorsList records are normalized into coordinator data."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 55.0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {
                "data": [
                    {
                        "uuid": "uuid-1",
                        "name": "CPU Temp",
                        "scriptpath": "/usr/local/bin/cpu-temp",
                        "divisor": 1000,
                        "widgetgroup": "CPU",
                        "currenttemp": "26.8 °C",
                    },
                    {
                        "uuid": "uuid-2",
                        "name": "NVMe Temp",
                        "scriptpath": "/usr/local/bin/nvme-temp",
                        "divisor": 1,
                        "widgetgroup": "",
                        "currenttemp": "38.0 °C",
                    },
                ],
                "total": 2,
            },
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    sensors = data["tempmon"]
    assert len(sensors) == 2

    cpu = next(s for s in sensors if s["name"] == "CPU Temp")
    assert cpu["sensor_key"] == "uuid-1"
    assert cpu["temperature"] == 26.8
    assert cpu["widgetgroup"] == "CPU"

    nvme = next(s for s in sensors if s["name"] == "NVMe Temp")
    assert nvme["sensor_key"] == "uuid-2"
    assert nvme["temperature"] == 38.0
    assert nvme["widgetgroup"] == ""


@pytest.mark.asyncio
async def test_tempmon_absent_when_plugin_not_installed(hass, config_entry) -> None:
    """Test that coordinator.data['tempmon'] is empty when the plugin is not installed."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("TempMon", "getSensorsList"):
            raise OMVApiError("RPC service TempMon not found")
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 55.0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["tempmon"] == []


@pytest.mark.asyncio
async def test_tempmon_script_error_returns_none_temperature(hass, config_entry) -> None:
    """Test that a sensor with a failing script (currenttemp='-') gets temperature=None."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 55.0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("TempMon", "getSensorsList"): {
                "data": [
                    {
                        "uuid": "uuid-1",
                        "name": "Broken Sensor",
                        "scriptpath": "/usr/local/bin/broken",
                        "divisor": 1,
                        "widgetgroup": "",
                        "currenttemp": "-",
                    }
                ],
                "total": 1,
            },
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert len(data["tempmon"]) == 1
    assert data["tempmon"][0]["temperature"] is None


@pytest.mark.asyncio
async def test_kvm_vms_normalized(hass, config_entry) -> None:
    """Test that Kvm.getVmList records are normalized into coordinator data."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 55.0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {
                "data": [
                    {
                        "uuid": "vm-uuid-1",
                        "name": "homeassistant",
                        "state": "Running",
                        "autostart": 1,
                        "memory": "2048",
                        "vcpu": "2",
                    },
                    {
                        "uuid": "",
                        "name": "scratch-vm",
                        "state": "Shut off",
                        "autostart": 0,
                    },
                ],
                "total": 2,
            },
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    vms = data["kvm"]
    assert len(vms) == 2

    running = next(vm for vm in vms if vm["vm_key"] == "vm-uuid-1")
    assert running["uuid"] == "vm-uuid-1"
    assert running["name"] == "homeassistant"
    assert running["state"] == "running"
    assert running["running"] is True
    assert running["autostart"] is True
    assert running["memory"] == 2048.0
    assert running["vcpu"] == 2.0

    stopped = next(vm for vm in vms if vm["vm_key"] == "scratch-vm")
    assert stopped["uuid"] == ""
    assert stopped["state"] == "shut_off"
    assert stopped["running"] is False
    assert stopped["autostart"] is False
    assert "memory" not in stopped
    assert "vcpu" not in stopped


@pytest.mark.asyncio
async def test_kvm_absent_when_plugin_not_installed(hass, config_entry) -> None:
    """Test that coordinator.data['kvm'] is empty when the KVM plugin is not installed."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None, **kwargs):
        if (service, method) == ("Kvm", "getVmList"):
            raise OMVApiError("RPC service Kvm not found")
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 55.0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("zfs", "listDatasets"): [],
            ("zfs", "getAllSnapshots"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["kvm"] == []


def test_build_inventory_includes_vms() -> None:
    """Test that build_inventory exposes normalized VMs for the options flow."""
    inventory = OMVDataUpdateCoordinator.build_inventory(
        {
            "kvm": [
                {"vm_key": "vm-uuid-1", "name": "homeassistant"},
                {"vm_key": "vm-uuid-2", "name": "scratch-vm"},
            ],
        }
    )

    assert [item["value"] for item in inventory[CONF_SELECTED_VMS]] == ["vm-uuid-1", "vm-uuid-2"]
    assert [item["label"] for item in inventory[CONF_SELECTED_VMS]] == ["homeassistant", "scratch-vm"]


@pytest.mark.asyncio
async def test_filter_data_by_selection_filters_vms(hass, config_entry) -> None:
    """Test that filter_data_by_selection honors CONF_SELECTED_VMS."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"
    api.async_call = AsyncMock()

    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    data = {
        "hwinfo": {},
        "disk": [],
        "fs": [],
        "service": [],
        "network": [],
        "raid": [],
        "zfs": [],
        "smart": [],
        "compose": [],
        "compose_projects": [],
        "compose_volumes": [],
        "kvm": [
            {"vm_key": "vm-uuid-1", "name": "homeassistant"},
            {"vm_key": "vm-uuid-2", "name": "scratch-vm"},
        ],
    }

    filtered = coordinator.filter_data_by_selection(data, {CONF_SELECTED_VMS: ["vm-uuid-1"]})

    assert [vm["vm_key"] for vm in filtered["kvm"]] == ["vm-uuid-1"]


@pytest.mark.asyncio
async def test_normalize_kvm_handles_real_plugin_payload(hass, config_entry) -> None:
    """The real openmediavault-kvm payload (vmname/mem/cpu) must normalize."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    vms = coordinator._normalize_kvm(
        [
            {
                "vmname": "homeassistant",
                "virttype": "vm",
                "mem": 2147483648,
                "cpu": 2,
                "state": "shut off",
                "autostart": True,
                "vncport": "n/a",
                "spiceport": "5901",
                "arch": "x86_64",
            }
        ]
    )

    assert len(vms) == 1
    vm = vms[0]
    assert vm["vm_key"] == "homeassistant"
    assert vm["name"] == "homeassistant"
    assert vm["state"] == "shut_off"
    assert vm["running"] is False
    assert vm["autostart"] is True
    assert vm["virttype"] == "vm"
    assert vm["vncport"] == "n/a"
    assert vm["spiceport"] == "5901"
    assert vm["memory"] == 2048
    assert vm["vcpu"] == 2.0


@pytest.mark.asyncio
async def test_normalize_kvm_handles_legacy_payload(hass, config_entry) -> None:
    """Legacy-shaped records (uuid/name/memory/vcpu) keep normalizing."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    vms = coordinator._normalize_kvm(
        [
            {
                "uuid": "vm-uuid-1234",
                "name": "homeassistant",
                "state": "running",
                "autostart": False,
                "memory": 2048.0,
                "vcpu": 2.0,
            }
        ]
    )

    assert len(vms) == 1
    vm = vms[0]
    assert vm["vm_key"] == "vm-uuid-1234"
    assert vm["name"] == "homeassistant"
    assert vm["running"] is True
    assert vm["virttype"] == "vm"
    assert vm["vncport"] == "n/a"
    assert vm["memory"] == 2048.0
    assert vm["vcpu"] == 2.0


@pytest.mark.asyncio
async def test_normalize_kvm_skips_records_without_any_name(hass, config_entry) -> None:
    """Records lacking vmname, uuid and name are dropped."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    assert coordinator._normalize_kvm([{"state": "running"}]) == []


@pytest.mark.asyncio
async def test_async_execute_vm_command_calls_kvm_do_command(hass, config_entry) -> None:
    """The VM command helper must send the exact Kvm.doCommand param dict."""
    api = Mock()
    api.async_call = AsyncMock(return_value=None)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    vm = {
        "vm_key": "homeassistant",
        "name": "homeassistant",
        "virttype": "vm",
        "vncport": "n/a",
        "spiceport": "5901",
    }
    await coordinator.async_execute_vm_command(vm, "poweron")

    api.async_call.assert_awaited_once_with(
        "Kvm",
        "doCommand",
        {
            "command": "poweron",
            "name": "homeassistant",
            "virttype": "vm",
            "vncport": "n/a",
            "spiceport": "5901",
            "hostport": "n/a",
            "hostport2": "n/a",
        },
    )


@pytest.mark.asyncio
async def test_normalize_nut_parses_upsc_output(hass, config_entry) -> None:
    """Raw upsc output must yield charge, runtime, load and online status."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    nut = coordinator._normalize_nut(
        "battery.charge: 100\nbattery.runtime: 1320\ndevice.model: Eaton 5E\nups.load: 23\nups.status: OL\n"
    )

    assert nut["battery_charge"] == 100.0
    assert nut["battery_runtime"] == 1320.0
    assert nut["load"] == 23.0
    assert nut["status"] == "OL"
    assert nut["on_battery"] is False
    assert nut["model"] == "Eaton 5E"
    assert nut["raw"]["ups.status"] == "OL"


@pytest.mark.asyncio
async def test_normalize_nut_detects_on_battery(hass, config_entry) -> None:
    """An 'OB DISCHRG' ups.status must flag on_battery."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    nut = coordinator._normalize_nut("battery.charge: 87\nups.status: OB DISCHRG\nups.load: 31\n")

    assert nut["on_battery"] is True
    assert nut["status"] == "OB DISCHRG"
    assert nut["battery_charge"] == 87.0


@pytest.mark.asyncio
async def test_normalize_nut_treats_disabled_and_missing_as_empty(hass, config_entry) -> None:
    """Localized 'Service disabled' strings and absent RPCs yield an empty dict."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    assert coordinator._normalize_nut("Service disabled") == {}
    assert coordinator._normalize_nut("Dienst deaktiviert") == {}
    assert coordinator._normalize_nut([]) == {}
    assert coordinator._normalize_nut(None) == {}


@pytest.mark.asyncio
async def test_filter_data_passes_nut_through(hass, config_entry) -> None:
    """filter_data_by_selection must pass the nut dict through unchanged."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    nut = {"battery_charge": 100.0, "status": "OL", "on_battery": False}
    filtered = coordinator.filter_data_by_selection({"nut": nut}, {})

    assert filtered["nut"] == nut


@pytest.mark.asyncio
async def test_normalize_rsync_builds_records_with_name_fallbacks(hass, config_entry) -> None:
    """Rsync records must carry a stable key, name fallbacks and a schedule string."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    jobs = coordinator._normalize_rsync(
        [
            {
                "uuid": "uuid-with-comment",
                "enable": True,
                "comment": "Backup media",
                "type": "local",
                "mode": "push",
                "srcname": "/srv/media",
                "destname": "/srv/backup",
                "minute": "0",
                "hour": "3",
                "dayofmonth": "*",
                "month": "*",
                "dayofweek": "*",
            },
            {
                "uuid": "uuid-no-comment",
                "enable": False,
                "comment": "",
                "type": "remote",
                "mode": "pull",
                "srcname": "host:/data",
                "destname": "/srv/mirror",
                "minute": "30",
                "hour": "4",
                "dayofmonth": "*",
                "month": "*",
                "dayofweek": "0",
            },
            {"uuid": "", "comment": "no uuid -> dropped"},
        ]
    )

    assert len(jobs) == 2
    assert jobs[0]["rsync_key"] == "uuid-with-comment"
    assert jobs[0]["name"] == "Backup media"
    assert jobs[0]["enabled"] is True
    assert jobs[0]["schedule"] == "0 3 * * *"
    assert jobs[1]["name"] == "host:/data → /srv/mirror"
    assert jobs[1]["enabled"] is False
    assert jobs[1]["mode"] == "pull"
    assert jobs[1]["schedule"] == "30 4 * * 0"


@pytest.mark.asyncio
async def test_normalize_rsync_handles_absent_rpc(hass, config_entry) -> None:
    """An absent Rsync RPC ([] response) must yield an empty job list."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    assert coordinator._normalize_rsync([]) == []
    assert coordinator._normalize_rsync(None) == []


@pytest.mark.asyncio
async def test_async_execute_rsync_job_calls_rsync_execute(hass, config_entry) -> None:
    """The rsync job helper must call Rsync.execute with the job uuid."""
    api = Mock()
    api.async_call = AsyncMock(return_value="/tmp/bgstatusXYZ")
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    result = await coordinator.async_execute_rsync_job("rsync-uuid-0001")

    api.async_call.assert_awaited_once_with("Rsync", "execute", {"uuid": "rsync-uuid-0001"})
    assert result == "/tmp/bgstatusXYZ"


@pytest.mark.asyncio
async def test_filter_data_passes_rsync_through(hass, config_entry) -> None:
    """filter_data_by_selection must pass rsync jobs through unfiltered."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    jobs = [{"rsync_key": "rsync-uuid-0001", "name": "Backup media", "enabled": True}]
    filtered = coordinator.filter_data_by_selection({"rsync": jobs}, {})

    assert filtered["rsync"] == jobs


@pytest.mark.asyncio
async def test_normalize_cron_builds_records_with_name_fallbacks(hass, config_entry) -> None:
    """Cron records must carry a stable key, name fallbacks and a schedule string."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    long_command = "/usr/local/bin/very-long-maintenance-script.sh --all"
    jobs = coordinator._normalize_cron(
        [
            {
                "uuid": "uuid-with-comment",
                "enable": True,
                "comment": "Nightly cleanup",
                "command": "/usr/local/bin/cleanup.sh",
                "username": "root",
                "minute": "0",
                "hour": "2",
                "dayofmonth": "*",
                "month": "*",
                "dayofweek": "*",
            },
            {
                "uuid": "uuid-long-command",
                "enable": False,
                "comment": "",
                "command": long_command,
                "username": "root",
                "minute": "15",
                "hour": "5",
                "dayofmonth": "*",
                "month": "*",
                "dayofweek": "6",
            },
            {
                "uuid": "uuid-no-comment-no-command",
                "enable": True,
                "comment": "",
                "command": "",
            },
            {"uuid": "", "comment": "no uuid -> dropped"},
        ]
    )

    assert len(jobs) == 3
    assert jobs[0]["cron_key"] == "uuid-with-comment"
    assert jobs[0]["name"] == "Nightly cleanup"
    assert jobs[0]["enabled"] is True
    assert jobs[0]["command"] == "/usr/local/bin/cleanup.sh"
    assert jobs[0]["username"] == "root"
    assert jobs[0]["schedule"] == "0 2 * * *"
    assert jobs[1]["name"] == f"{long_command[:37]}..."
    assert jobs[1]["enabled"] is False
    assert jobs[1]["schedule"] == "15 5 * * 6"
    assert jobs[2]["name"] == "uuid-no-comment-no-command"
    assert jobs[2]["schedule"] == "* * * * *"


@pytest.mark.asyncio
async def test_normalize_cron_handles_absent_rpc(hass, config_entry) -> None:
    """An absent Cron RPC ([] response) must yield an empty job list."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    assert coordinator._normalize_cron([]) == []
    assert coordinator._normalize_cron(None) == []


@pytest.mark.asyncio
async def test_async_execute_cron_job_calls_cron_execute(hass, config_entry) -> None:
    """The cron job helper must call Cron.execute with the job uuid."""
    api = Mock()
    api.async_call = AsyncMock(return_value="/tmp/bgstatusCRON")
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    result = await coordinator.async_execute_cron_job("cron-uuid-0001")

    api.async_call.assert_awaited_once_with("Cron", "execute", {"uuid": "cron-uuid-0001"})
    assert result == "/tmp/bgstatusCRON"


@pytest.mark.asyncio
async def test_filter_data_passes_cron_through(hass, config_entry) -> None:
    """filter_data_by_selection must pass cron jobs through unfiltered.

    The cron selection is an opt-in for button creation, not a data filter —
    even with other selections present, all jobs must survive.
    """
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    jobs = [
        {"cron_key": "cron-uuid-0001", "name": "Nightly cleanup", "enabled": True},
        {"cron_key": "cron-uuid-0002", "name": "Weekly task", "enabled": False},
    ]
    filtered = coordinator.filter_data_by_selection({"cron": jobs}, {"selected_cron_jobs": ["cron-uuid-0001"]})

    assert filtered["cron"] == jobs


@pytest.mark.asyncio
async def test_normalize_zfs_datasets_uses_path_keys_and_filters_types(hass, config_entry) -> None:
    """Dataset keys must be plain paths; non-Filesystem/Volume records are dropped."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    datasets = coordinator._normalize_zfs_datasets(
        [
            {
                "id": "root/pool-tank/poolfs-tank-media",
                "name": "media",
                "path": "tank/media",
                "type": "Filesystem",
                "used": str(420 * 1024**3),
                "available": str(580 * 1024**3),
                "mountpoint": "/srv/tank/media",
                "compression": "lz4",
                "encryption": "off",
            },
            {
                "id": "root/pool-tank/vol-tank-swap",
                "name": "swap",
                "path": "tank/swap",
                "type": "Volume",
                "used": str(8 * 1024**3),
                "available": str(580 * 1024**3),
                "mountpoint": "",
                "compression": "off",
                "encryption": "on",
            },
            {"id": "root/pool-tank", "name": "tank", "path": "tank", "type": "Pool"},
            {"id": "snap", "name": "s1", "path": "tank/media@s1", "type": "Snapshot"},
            {"name": "", "path": "", "type": "Filesystem"},
        ]
    )

    assert [d["dataset_key"] for d in datasets] == ["tank/media", "tank/swap"]
    assert datasets[0]["pool"] == "tank"
    assert datasets[0]["used_gb"] == pytest.approx(451.0, abs=1.0)
    assert datasets[0]["encrypted"] is False
    assert datasets[1]["type"] == "Volume"
    assert datasets[1]["encrypted"] is True
    assert datasets[1]["mountpoint"] == ""


@pytest.mark.asyncio
async def test_normalize_zfs_snapshots_aggregates_per_pool(hass, config_entry) -> None:
    """Snapshots aggregate to per-pool counts, including root-dataset snapshots."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    counts = coordinator._normalize_zfs_snapshots(
        [
            {"type": "Snapshot", "path": "tank/media@daily-1"},
            {"type": "Snapshot", "path": "tank/media@daily-2"},
            {"type": "Snapshot", "path": "tank@first"},
            {"type": "Snapshot", "path": "backup/docs@weekly"},
            {"type": "Filesystem", "path": "tank/media"},
            {"type": "Snapshot", "path": ""},
        ]
    )

    assert counts == {"tank": 3, "backup": 1}
    assert coordinator._normalize_zfs_snapshots([]) == {}


@pytest.mark.asyncio
async def test_async_scrub_zfs_pool_uses_plain_pool_name(hass, config_entry) -> None:
    """The scrub helper must call zfs.scrubPool with the plain pool name."""
    api = Mock()
    api.async_call = AsyncMock(return_value=None)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    await coordinator.async_scrub_zfs_pool("tank")

    api.async_call.assert_awaited_once_with("zfs", "scrubPool", {"name": "tank"})


@pytest.mark.asyncio
async def test_filter_data_zfs_datasets_follow_pool_selection(hass, config_entry) -> None:
    """Datasets survive only when their owning pool survives the zfs filter."""
    api = Mock()
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)

    data = {
        "zfs": [{"name": "tank"}, {"name": "backup"}],
        "zfs_datasets": [
            {"dataset_key": "tank/media", "pool": "tank"},
            {"dataset_key": "backup/docs", "pool": "backup"},
        ],
    }

    filtered = coordinator.filter_data_by_selection(data, {"selected_zfs_pools": ["tank"]})
    assert [p["name"] for p in filtered["zfs"]] == ["tank"]
    assert [d["dataset_key"] for d in filtered["zfs_datasets"]] == ["tank/media"]

    unfiltered = coordinator.filter_data_by_selection(data, {})
    assert len(unfiltered["zfs_datasets"]) == 2

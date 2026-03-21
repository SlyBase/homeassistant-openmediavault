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
    CONF_SELECTED_ZFS_POOLS,
    DOMAIN,
)
from custom_components.omv.coordinator import OMVDataUpdateCoordinator


@pytest.mark.asyncio
async def test_coordinator_fetches_expected_data(hass, config_entry) -> None:
    """Test the coordinator normalizes the main OMV payloads."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None):
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
            ("zfs", "listPools"): [{"name": "tank", "state": "ONLINE"}],
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
        smart_disabled=True,
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
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60, smart_disabled=True)

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

    async def async_call(service, method, params=None):
        if (service, method) == ("Compose", "doContainerCommand"):
            return "/tmp/bgstatusSOhiV3"
        if (service, method) == ("Exec", "getOutput"):
            return {"running": False, "output": shell_header + json.dumps(nginx_inspect)}
        raise AssertionError((service, method, params))

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60, smart_disabled=True)

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

    async def async_call(service, method, params=None):
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
        smart_disabled=True,
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

    async def async_call(service, method, params=None):
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
        smart_disabled=True,
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
        smart_disabled=True,
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
        smart_disabled=True,
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
        smart_disabled=True,
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
        smart_disabled=True,
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

    async def async_call(service, method, params=None):
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
        smart_disabled=True,
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

    async def async_call(service, method, params=None):
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
            ("zfs", "listPools"): [],
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

    async def async_call(service, method, params=None):
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
            ("zfs", "listPools"): [],
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
        smart_disabled=True,
    )
    coordinator.data = coordinator.filter_data_by_selection(
        {
            "hwinfo": {},
            "disk": [
                {"disk_key": "sda", "devicename": "sda"},
                {"disk_key": "sdb", "devicename": "sdb"},
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

    assert [item["value"] for item in inventory[CONF_SELECTED_DISKS]] == ["sda", "sdb"]
    assert [disk["disk_key"] for disk in coordinator.data["disk"]] == ["sda"]
    assert [filesystem["uuid"] for filesystem in coordinator.data["fs"]] == ["fs-1"]
    assert [item["value"] for item in inventory[CONF_SELECTED_CONTAINERS]] == [
        "ctr-paperless-app",
        "ctr-web-nginx",
    ]
    assert [container["container_key"] for container in coordinator.data["compose"]] == ["ctr-paperless-app"]
    assert [project["project_key"] for project in coordinator.data["compose_projects"]] == ["paperless"]
    assert [volume["volume_key"] for volume in coordinator.data["compose_volumes"]] == ["ctr-paperless-app:data"]


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
        smart_disabled=True,
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

    async def async_call(service, method, params=None):
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
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
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
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
        smart_disabled=True,
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

    async def async_call(service, method, params=None):
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
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
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
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
        smart_disabled=True,
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
        smart_disabled=True,
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
        smart_disabled=True,
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
        smart_disabled=True,
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
        smart_disabled=True,
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
async def test_virtual_passthrough_disables_cpu_temp_and_smart_calls(hass, config_entry) -> None:
    """Test virtual passthrough suppresses SMART and CPU temperature fetching."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"

    async def async_call(service, method, params=None):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("zfs", "listPools"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
        smart_disabled=False,
        virtual_passthrough=True,
    )
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["hwinfo"]["cputemp"] == 0.0
    assert not any(call.args[:2] == ("CpuTemp", "get") for call in api.async_call.await_args_list)
    assert not any(call.args[:2] == ("Smart", "getListBg") for call in api.async_call.await_args_list)


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

    await coordinator._async_get_smart(disks)

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

    async def async_call(service, method, params=None):
        if method == "getListBg":
            return {"data": [{"devicename": "sda", "temperature": 35, "overallstatus": "PASSED"}]}
        if method == "getAttributes":
            return {"data": [{"attrname": "Raw_Read_Error_Rate", "rawvalue": "0"}]}
        return []

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator.omv_version = 8

    await coordinator._async_get_smart(disks)

    assert any(
        len(call.args) >= 2 and call.args[0] == "Smart" and call.args[1] == "getAttributes"
        for call in api.async_call.await_args_list
    ), "getAttributes was not called for a regular disk"


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
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60, smart_disabled=True)

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
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60, smart_disabled=True)

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
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60, smart_disabled=True)

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

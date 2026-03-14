"""Tests for the OMV data coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.omv.coordinator import OMVDataUpdateCoordinator


@pytest.mark.asyncio
async def test_coordinator_fetches_expected_data(hass, config_entry) -> None:
    """Test the coordinator normalizes the main OMV payloads."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"

    async def async_call(service, method, params=None):
        responses = {
            ("System", "getInformation"): {
                "hostname": "nas",
                "version": "8.1.2-1",
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
                    "devicename": "mapper/data",
                    "available": 50 * 1073741824,
                    "size": 100 * 1073741824,
                    "percentage": 50,
                    "mountdir": "/srv/data",
                }
            ],
            ("Services", "getStatus"): [{"name": "ssh", "title": "SSH", "running": True, "enabled": True}],
            ("Network", "enumerateDevices"): [
                {
                    "uuid": "net-1",
                    "devicename": "eth0",
                    "type": "ethernet",
                    "stats": {"rx_bytes": 1000, "tx_bytes": 500},
                }
            ],
            ("DiskMgmt", "enumerateDevices"): [
                {"devicename": "sda", "canonicaldevicefile": "/dev/sda", "model": "Disk"}
            ],
            ("Smart", "getListBg"): [{"devicename": "sda", "temperature": 32, "overallstatus": "PASSED"}],
            ("Smart", "getAttributes"): [{"attrname": "Raw_Read_Error_Rate", "rawvalue": "0 0 0"}],
            ("compose", "getContainerList"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("zfs", "listPools"): [{"name": "tank", "state": "ONLINE"}],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)
    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["hwinfo"]["pkgUpdatesAvailable"] is True
    assert data["hwinfo"]["rebootRequired"] is True
    assert data["fs"][0]["devicename"] == "data"
    assert data["disk"][0]["overallstatus"] == "PASSED"
    assert data["disk"][0]["Raw_Read_Error_Rate"] == "0"
    assert data["zfs"][0]["name"] == "tank"


@pytest.mark.asyncio
async def test_coordinator_uses_legacy_smart_method_for_omv6(hass, config_entry) -> None:
    """Test OMV6 falls back to Smart.getList."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"

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
            ("Kvm", "getVmList"): {"data": []},
            ("zfs", "listPools"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)

    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "6.9.0"})

    await coordinator._async_update_data()

    assert any(call.args[:2] == ("Smart", "getList") for call in api.async_call.await_args_list)


@pytest.mark.asyncio
async def test_coordinator_falls_back_when_smart_get_list_bg_returns_task_id(
    hass, config_entry
) -> None:
    """Test OMV7+ falls back to Smart.getList when getListBg returns a task id."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"

    async def async_call(service, method, params=None):
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): [],
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [
                {"devicename": "sda", "canonicaldevicefile": "/dev/sda"}
            ],
            ("Smart", "getListBg"): "task-123",
            ("Smart", "getList"): {
                "data": [
                    {"devicename": "sda", "temperature": 32, "overallstatus": "GOOD"}
                ]
            },
            ("Smart", "getAttributes"): [],
            ("compose", "getContainerList"): {"data": []},
            ("Kvm", "getVmList"): {"data": []},
            ("zfs", "listPools"): [],
        }
        return responses[(service, method)]

    api.async_call = AsyncMock(side_effect=async_call)

    coordinator = OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["disk"][0]["overallstatus"] == "GOOD"
    assert any(call.args[:2] == ("Smart", "getListBg") for call in api.async_call.await_args_list)
    assert any(
        call.args == ("Smart", "getList", {"start": 0, "limit": 100})
        for call in api.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_network_rates_are_calculated_from_previous_counters(hass, config_entry) -> None:
    """Test network rates use deltas between refreshes."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"

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
    assert second[0]["rx"] == 80.0
    assert second[0]["tx"] == 80.0
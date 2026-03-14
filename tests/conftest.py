"""Shared fixtures for OMV integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import custom_components
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv.const import DOMAIN
from custom_components.omv.coordinator import OMVDataUpdateCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations from the workspace."""
    yield


@pytest.fixture(autouse=True)
def fix_custom_components_namespace() -> None:
    """Ensure HA only scans real custom_components directories during tests."""
    workspace_custom_components = Path(__file__).resolve().parents[1] / "custom_components"
    original_path = custom_components.__path__
    custom_components.__path__ = [str(workspace_custom_components)]
    try:
        yield
    finally:
        custom_components.__path__ = original_path


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a mock OMV config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data={
            CONF_HOST: "192.168.1.10",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_PORT: 80,
            CONF_SSL: False,
            CONF_VERIFY_SSL: True,
        },
        options={},
    )


@pytest.fixture
def sample_data() -> dict[str, Any]:
    """Return normalized coordinator sample data."""
    return {
        "hwinfo": {
            "hostname": "nas",
            "version": "8.1.2-1",
            "cpuUtilization": 15.3,
            "cputemp": 45.1,
            "memUsage": 62.5,
            "loadAverage": {"1min": 0.1, "5min": 0.2, "15min": 0.3},
            "uptimeEpoch": "2026-03-13T12:00:00+00:00",
            "pkgUpdatesAvailable": True,
            "rebootRequired": False,
        },
        "fs": [
            {
                "uuid": "fs-1",
                "label": "data",
                "type": "ext4",
                "size": 100.0,
                "used": 40.0,
                "available": 60.0,
                "percentage": 40.0,
                "mountdir": "/srv/data",
            }
        ],
        "service": [
            {"name": "ssh", "title": "SSH", "enabled": True, "running": True}
        ],
        "network": [
            {
                "uuid": "net-1",
                "devicename": "eth0",
                "type": "ethernet",
                "method": "static",
                "address": "192.168.1.10",
                "netmask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "mtu": 1500,
                "link": True,
                "wol": False,
                "rx": 128.0,
                "tx": 64.0,
            }
        ],
        "disk": [
            {
                "devicename": "sda",
                "canonicaldevicefile": "/dev/sda",
                "temperature": 34.0,
                "model": "Disk Model",
                "serialnumber": "ABC123",
                "size": "1000 GB",
                "vendor": "ATA",
                "overallstatus": "PASSED",
                "Raw_Read_Error_Rate": "0",
                "Spin_Up_Time": "0",
                "Start_Stop_Count": "0",
                "Reallocated_Sector_Ct": "0",
                "Seek_Error_Rate": "0",
                "Load_Cycle_Count": "0",
                "UDMA_CRC_Error_Count": "0",
                "Multi_Zone_Error_Rate": "0",
            }
        ],
        "smart": [{"devicename": "sda", "temperature": 34, "overallstatus": "PASSED"}],
        "compose": [],
        "kvm": [],
        "zfs": [{"name": "tank", "state": "ONLINE", "size": "1T"}],
        "raid": [
            {
                "device": "md0",
                "state": "active",
                "level": "raid1",
                "health": "clean",
                "health_indicator": "UU",
                "action_percent": None,
            }
        ],
    }


@pytest.fixture
async def coordinator(hass, config_entry: MockConfigEntry, sample_data: dict[str, Any]) -> OMVDataUpdateCoordinator:
    """Create a configured coordinator with sample data."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.168.1.10:80"
    api.async_call = AsyncMock()

    coordinator = OMVDataUpdateCoordinator(
        hass,
        config_entry,
        api,
        scan_interval=60,
        smart_disabled=False,
    )
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})
    coordinator.data = sample_data
    config_entry.runtime_data = coordinator
    return coordinator
"""Shared fixtures for OMV integration tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import custom_components
import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv.const import DOMAIN
from custom_components.omv.coordinator import OMVDataUpdateCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations from the workspace."""
    yield


@pytest.fixture(autouse=True)
def fix_custom_components_namespace() -> Generator[None, None, None]:
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
            "cpuModel": "Intel(R) N100",
            "kernel": "Linux 6.6.0-omv",
            "cpuUtilization": 15.3,
            "cputemp": 45.1,
            "memTotal": 16000,
            "memUsed": 10000,
            "memUsage": 62.5,
            "loadAverage": {"1min": 0.1, "5min": 0.2, "15min": 0.3},
            "uptimeEpoch": datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc),
            "availablePkgUpdates": 3,
            "pkgUpdatesAvailable": True,
            "rebootRequired": False,
        },
        "fs": [
            {
                "uuid": "fs-1",
                "label": "data",
                "type": "ext4",
                "devicename": "sda1",
                "devicefile": "/dev/sda1",
                "canonicaldevicefile": "/dev/sda1",
                "parentdevicefile": "/dev/sda",
                "disk_key": "sda",
                "size": 100.0,
                "used": 40.0,
                "available": 60.0,
                "percentage": 40.0,
                "free_percentage": 60.0,
                "mountdir": "/srv/data",
            },
            {
                "uuid": "fs-2",
                "label": "external",
                "type": "ext4",
                "devicename": "mapper/external",
                "devicefile": "/dev/mapper/external",
                "canonicaldevicefile": "",
                "parentdevicefile": "",
                "disk_key": None,
                "size": 50.0,
                "used": 10.0,
                "available": 40.0,
                "percentage": 20.0,
                "free_percentage": 80.0,
                "mountdir": "/srv/external",
            },
        ],
        "service": [
            {"name": "ssh", "title": "SSH", "enabled": True, "running": True},
            {"name": "compose", "title": "Docker", "enabled": True, "running": True}
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
                "disk_key": "sda",
                "devicename": "sda",
                "devicefile": "/dev/sda",
                "canonicaldevicefile": "/dev/sda",
                "temperature": 34.0,
                "model": "Disk Model",
                "serialnumber": "ABC123",
                "size": "1000 GB",
                "total_size_gb": 100.0,
                "used_size_gb": 40.0,
                "free_size_gb": 60.0,
                "used_percentage": 40.0,
                "free_percentage": 60.0,
                "storage_source": "filesystem",
                "storage_label": "data",
                "vendor": "ATA",
                "overallstatus": "PASSED",
                "smart_details": {"temperature": 34, "overallstatus": "PASSED"},
                "smart_attributes": {"Raw_Read_Error_Rate": "0"},
                "Raw_Read_Error_Rate": "0",
                "Spin_Up_Time": "0",
                "Start_Stop_Count": "0",
                "Reallocated_Sector_Ct": "0",
                "Seek_Error_Rate": "0",
                "Load_Cycle_Count": "0",
                "UDMA_CRC_Error_Count": "0",
                "Multi_Zone_Error_Rate": "0",
            },
            {
                "disk_key": "sdb",
                "devicename": "sdb",
                "devicefile": "/dev/sdb",
                "canonicaldevicefile": "/dev/sdb",
                "temperature": None,
                "model": "Backup Disk",
                "serialnumber": "XYZ789",
                "size": "500 GB",
                "total_size_gb": 500.0,
                "used_size_gb": None,
                "free_size_gb": None,
                "used_percentage": None,
                "free_percentage": None,
                "storage_source": None,
                "storage_label": None,
                "vendor": "ATA",
                "overallstatus": "PASSED",
            },
            {
                "disk_key": "sdc",
                "devicename": "sdc",
                "devicefile": "/dev/sdc",
                "canonicaldevicefile": "/dev/sdc",
                "temperature": None,
                "model": "ZFS Disk",
                "serialnumber": "ZFS001",
                "size": "2000.4 GB",
                "total_size_gb": 2000.4,
                "used_size_gb": 1000.2,
                "free_size_gb": 1000.2,
                "used_percentage": 50.0,
                "free_percentage": 50.0,
                "storage_source": "zfs",
                "storage_label": "tank",
                "vendor": "ATA",
                "overallstatus": "PASSED",
            }
        ],
        "smart": [{"devicename": "sda", "temperature": 34, "overallstatus": "PASSED"}],
        "compose": [
            {
                "container_key": "ctr-paperless-app",
                "container_id": "ctr-paperless-app",
                "name": "paperless-app",
                "image": "ghcr.io/paperless-ngx/paperless-ngx:latest",
                "version": "2.15.3",
                "state": "running",
                "status_detail": "Up 5 minutes",
                "created_at": datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc),
                "started_at": datetime(2026, 3, 13, 10, 5, tzinfo=timezone.utc),
                "project_key": "paperless",
                "project_name": "paperless",
                "project_uuid": "proj-paperless",
                "project_status": "UP",
                "project_uptime": "Up 5 minutes",
                "compose_service": "webserver",
                "running": True,
            },
            {
                "container_key": "ctr-nginx",
                "container_id": "ctr-nginx",
                "name": "nginx",
                "image": "nginx:stable",
                "version": "1.27.4",
                "state": "running",
                "status_detail": "Up 5 minutes",
                "created_at": datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
                "started_at": datetime(2026, 3, 13, 9, 2, tzinfo=timezone.utc),
                "project_key": "web",
                "project_name": "web",
                "project_uuid": "proj-web",
                "project_status": "UP",
                "project_uptime": "Up 5 minutes",
                "compose_service": "proxy",
                "running": True,
            },
            {
                "container_key": "ctr-vaultwarden",
                "container_id": "ctr-vaultwarden",
                "name": "vaultwarden",
                "image": "vaultwarden/server:latest",
                "version": "1.33.2",
                "state": "running",
                "status_detail": "Up 10 minutes",
                "created_at": datetime(2026, 3, 13, 11, 0, tzinfo=timezone.utc),
                "started_at": datetime(2026, 3, 13, 11, 1, tzinfo=timezone.utc),
                "project_key": "vaultwarden",
                "project_name": "vaultwarden",
                "project_uuid": "proj-vaultwarden",
                "project_status": "UP",
                "project_uptime": "Up 10 minutes",
                "compose_service": "vaultwarden",
                "running": True,
            },
            {
                "container_key": "ctr-db",
                "container_id": "ctr-db",
                "name": "db",
                "image": "postgres:16",
                "version": "16.4",
                "state": "exited",
                "status_detail": "Exited (0) 2 hours ago",
                "created_at": datetime(2026, 3, 12, 18, 0, tzinfo=timezone.utc),
                "started_at": datetime(2026, 3, 12, 18, 3, tzinfo=timezone.utc),
                "project_key": "paperless",
                "project_name": "paperless",
                "project_uuid": "proj-paperless",
                "project_status": "UP",
                "project_uptime": "Up 5 minutes",
                "compose_service": "db",
                "running": False,
            },
        ],
        "compose_projects": [
            {
                "project_key": "paperless",
                "name": "paperless",
                "uuid": "proj-paperless",
                "status": "UP",
                "uptime": "Up 5 minutes",
                "service_name": "webserver",
                "image": "ghcr.io/paperless-ngx/paperless-ngx:latest",
                "container_total": 2,
                "container_running": 1,
                "container_not_running": 1,
            },
            {
                "project_key": "web",
                "name": "web",
                "uuid": "proj-web",
                "status": "UP",
                "uptime": "Up 5 minutes",
                "service_name": "proxy",
                "image": "nginx:stable",
                "container_total": 1,
                "container_running": 1,
                "container_not_running": 0,
            },
            {
                "project_key": "vaultwarden",
                "name": "vaultwarden",
                "uuid": "proj-vaultwarden",
                "status": "UP",
                "uptime": "Up 10 minutes",
                "service_name": "vaultwarden",
                "image": "vaultwarden/server:latest",
                "container_total": 1,
                "container_running": 1,
                "container_not_running": 0,
            },
        ],
        "compose_summary": {
            "total": 4,
            "running": 3,
            "not_running": 1,
        },
        "compose_volumes": [
            {
                "volume_key": "ctr-paperless-app:paperless_data",
                "display_name": "paperless_data",
                "name": "paperless_data",
                "size_gb": 12.3,
                "source": "paperless_data",
                "destination": "/usr/src/paperless/data",
                "container_key": "ctr-paperless-app",
                "container_name": "paperless-app",
                "project_key": "paperless",
                "project_name": "paperless",
                "image": "ghcr.io/paperless-ngx/paperless-ngx:latest",
                "version": "2.15.3",
            },
            {
                "volume_key": "ctr-vaultwarden:vaultwarden_data",
                "display_name": "vaultwarden_data",
                "name": "vaultwarden_data",
                "size_gb": 5.2,
                "source": "vaultwarden_data",
                "destination": "/data",
                "container_key": "ctr-vaultwarden",
                "container_name": "vaultwarden",
                "project_key": "vaultwarden",
                "project_name": "vaultwarden",
                "image": "vaultwarden/server:latest",
                "version": "1.33.2",
            },
            {
                "volume_key": "ctr-db:pg_data",
                "display_name": "pg_data",
                "name": "pg_data",
                "size_gb": 8.2,
                "source": "pg_data",
                "destination": "/var/lib/postgresql/data",
                "container_key": "ctr-db",
                "container_name": "db",
                "project_key": "paperless",
                "project_name": "paperless",
                "image": "postgres:16",
                "version": "16.4",
            },
            {
                "volume_key": "ctr-db:pg_backups",
                "display_name": "pg_backups",
                "name": "pg_backups",
                "size_gb": 3.4,
                "source": "pg_backups",
                "destination": "/backups",
                "container_key": "ctr-db",
                "container_name": "db",
                "project_key": "paperless",
                "project_name": "paperless",
                "image": "postgres:16",
                "version": "16.4",
            },
        ],
        "kvm": [],
        "zfs": [
            {
                "name": "tank",
                "state": "ONLINE",
                "size": 2000.4,
                "alloc": 1000.2,
                "free": 1000.2,
                "available": 1000.2,
                "capacity": 50.0,
                "mountpoint": "/srv/tank",
                "disk_key": "sdc",
            }
        ],
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
async def coordinator(
    hass,
    config_entry: MockConfigEntry,
    sample_data: dict[str, Any],
) -> OMVDataUpdateCoordinator:
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
        virtual_passthrough=False,
    )
    await coordinator.async_init(
        {
            "hostname": "nas",
            "version": "8.1.2-1",
            "cpuModelName": "Intel(R) N100",
            "kernel": "Linux 6.6.0-omv",
        }
    )
    coordinator.data = sample_data
    coordinator._inventory_source = sample_data
    config_entry.runtime_data = coordinator
    return coordinator
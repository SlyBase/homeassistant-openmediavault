"""Shared fixtures for OMV integration tests."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components
from custom_components.omv import session_handoff
from custom_components.omv.const import DOMAIN
from custom_components.omv.coordinator import OMVDataUpdateCoordinator
from custom_components.omv.entity import get_compose_project_device_info, get_hub_device_info


def _patch_aioresponses_for_aiohttp_314() -> None:
    """Work around aioresponses 0.7.9 missing aiohttp 3.14's ``stream_writer`` kwarg.

    aiohttp 3.14 made ``ClientResponse.__init__`` require a keyword-only
    ``stream_writer`` argument; aioresponses 0.7.9 (latest on PyPI) does not
    pass it yet, so every mocked response raises ``TypeError``. Mirrors the
    unreleased upstream fix (pnuckowski/aioresponses#288) until a compatible
    release ships — remove this once ``aioresponses`` is bumped past 0.7.9.
    """
    import inspect as _inspect
    from typing import Any as _Any
    from unittest.mock import Mock as _Mock

    from aioresponses import core as _aioresponses_core

    if "stream_writer" not in _inspect.signature(_aioresponses_core.ClientResponse.__init__).parameters:
        return

    _original_response_init = _aioresponses_core.ClientResponse.__init__

    def _patched_response_init(self: _Any, *args: _Any, **kwargs: _Any) -> None:
        kwargs.setdefault("stream_writer", _Mock(output_size=0))
        _original_response_init(self, *args, **kwargs)

    _aioresponses_core.ClientResponse.__init__ = _patched_response_init


_patch_aioresponses_for_aiohttp_314()


@pytest.fixture(autouse=True)
def _clear_session_handoff() -> Generator[None]:
    """Reset the module-level session hand-off registry between tests."""
    session_handoff._pending.clear()
    yield
    session_handoff._pending.clear()


@pytest.fixture(autouse=True)
async def _drain_aiohttp_on_teardown() -> AsyncGenerator[None]:
    """Drain aiohttp cleanup before verify_cleanup checks for lingering threads.

    In older aiohttp (< 3.11), BaseConnector.__del__ spawns a
    _run_safe_shutdown_loop background thread when the cyclic garbage collector
    collects an unclosed connector after the event loop has gone away.  By
    forcing gc.collect() and draining the event loop here — while still inside
    an async context so the loop is running — the connector's __del__ uses
    loop.call_soon_threadsafe() instead of starting a background thread.

    This fixture is set up AFTER pytest-homeassistant-custom-component's
    verify_cleanup (plugin fixtures register before conftest fixtures) and
    therefore tears down FIRST (LIFO), giving the event loop a clean drain
    before the thread-check assertion runs.
    """
    yield
    gc.collect()
    for _ in range(10):
        await asyncio.sleep(0)
    gc.collect()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations from the workspace."""
    yield


@pytest.fixture(autouse=True)
def fix_custom_components_namespace() -> Generator[None]:
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
            "uptimeEpoch": datetime(2026, 3, 13, 12, 0, tzinfo=UTC),
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
            {"name": "compose", "title": "Docker", "enabled": True, "running": True},
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
                "mac": "aa:bb:cc:dd:ee:ff",
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
            },
            {
                "disk_key": "nvme0n1",
                "devicename": "nvme0n1",
                "devicefile": "/dev/nvme0n1",
                "canonicaldevicefile": "/dev/nvme0n1",
                "temperature": 30.0,
                "model": "NVMe SSD Model",
                "serialnumber": "NVME001",
                "size": "2000 GB",
                "total_size_gb": 2000.0,
                "used_size_gb": None,
                "free_size_gb": None,
                "used_percentage": None,
                "free_percentage": None,
                "storage_source": None,
                "storage_label": None,
                "vendor": "NVMe",
                "overallstatus": "PASSED",
                "nvme_health": {
                    "available_spare": 100,
                    "available_spare_threshold": 10,
                    "percentage_used": 98,
                    "data_units_read": 585673910,
                    "data_units_written": 1892791876,
                    "power_on_hours": 24222,
                    "unsafe_shutdowns": 26,
                    "media_errors": 0,
                },
                "wear_percent": 98.0,
                "data_written_tb": 969.11,
                "data_read_tb": 299.87,
            },
            {
                "disk_key": "sdd",
                "devicename": "sdd",
                "devicefile": "/dev/sdd",
                "canonicaldevicefile": "/dev/sdd",
                "temperature": 28.0,
                "model": "SATA SSD Model",
                "serialnumber": "SSD001",
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
                "smart_attributes": {
                    "Wear_Leveling_Count": "1200",
                    "Wear_Leveling_Count__value": "97",
                    "Total_LBAs_Written": "78156288000",
                    "Total_LBAs_Read": "39078144000",
                },
                "Wear_Leveling_Count": "1200",
                "Wear_Leveling_Count__value": "97",
                "Total_LBAs_Written": "78156288000",
                "Total_LBAs_Read": "39078144000",
                "wear_percent": 3.0,
                "data_written_tb": 40.02,
                "data_read_tb": 20.01,
            },
            {
                "disk_key": "md0",
                "devicename": "md0",
                "devicefile": "/dev/md0",
                "canonicaldevicefile": "/dev/md0",
                "temperature": None,
                "model": "Linux MD RAID",
                "serialnumber": "md0",
                "size": "2000 GB",
                "total_size_gb": 2000.0,
                "used_size_gb": 1000.0,
                "free_size_gb": 1000.0,
                "used_percentage": 50.0,
                "free_percentage": 50.0,
                "storage_source": "filesystem",
                "storage_label": "data",
                "vendor": "OpenMediaVault",
                "overallstatus": "unknown",
                "israid": True,
                "is_logical": True,
            },
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
                "created_at": datetime(2026, 3, 13, 10, 0, tzinfo=UTC),
                "started_at": datetime(2026, 3, 13, 10, 5, tzinfo=UTC),
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
                "created_at": datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
                "started_at": datetime(2026, 3, 13, 9, 2, tzinfo=UTC),
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
                "created_at": datetime(2026, 3, 13, 11, 0, tzinfo=UTC),
                "started_at": datetime(2026, 3, 13, 11, 1, tzinfo=UTC),
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
                "created_at": datetime(2026, 3, 12, 18, 0, tzinfo=UTC),
                "started_at": datetime(2026, 3, 12, 18, 3, tzinfo=UTC),
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
        "kvm": [
            {
                "vm_key": "vm-uuid-1234",
                "uuid": "vm-uuid-1234",
                "name": "homeassistant",
                "state": "running",
                "running": True,
                "autostart": True,
                "virttype": "vm",
                "vncport": "n/a",
                "spiceport": "n/a",
                "memory": 2048.0,
                "vcpu": 2.0,
            },
        ],
        "nut": {
            "battery_charge": 100.0,
            "battery_runtime": 1320.0,
            "load": 23.0,
            "status": "OL",
            "on_battery": False,
            "model": "Eaton 5E",
            "raw": {
                "battery.charge": "100",
                "battery.runtime": "1320",
                "ups.load": "23",
                "ups.status": "OL",
                "device.model": "Eaton 5E",
            },
        },
        "rsync": [
            {
                "rsync_key": "rsync-uuid-0001",
                "uuid": "rsync-uuid-0001",
                "name": "Backup media",
                "enabled": True,
                "type": "local",
                "mode": "push",
                "srcname": "/srv/dev-disk-by-uuid-1/media",
                "destname": "/srv/dev-disk-by-uuid-2/backup",
                "schedule": "0 3 * * *",
            },
            {
                "rsync_key": "rsync-uuid-0002",
                "uuid": "rsync-uuid-0002",
                "name": "/srv/docs → /srv/backup/docs",
                "enabled": False,
                "type": "local",
                "mode": "push",
                "srcname": "/srv/docs",
                "destname": "/srv/backup/docs",
                "schedule": "30 4 * * 0",
            },
        ],
        "cron": [
            {
                "cron_key": "cron-uuid-0001",
                "uuid": "cron-uuid-0001",
                "name": "Nightly cleanup",
                "enabled": True,
                "command": "/usr/local/bin/cleanup.sh",
                "username": "root",
                "schedule": "0 2 * * *",
            },
            {
                "cron_key": "cron-uuid-0002",
                "uuid": "cron-uuid-0002",
                "name": "/usr/local/bin/very-long-maintenance-...",
                "enabled": False,
                "command": "/usr/local/bin/very-long-maintenance-script.sh --all",
                "username": "root",
                "schedule": "15 5 * * 6",
            },
        ],
        "upgradedList": [],
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
                "lastscrub": "Sun Jun  8 03:00:42 2026",
                "scrubactive": False,
                "scrubstate": "completed",
                "dataset_count": 2,
                "snapshot_count": 5,
            }
        ],
        "zfs_datasets": [
            {
                "dataset_key": "tank/media",
                "name": "media",
                "path": "tank/media",
                "pool": "tank",
                "used_gb": 420.5,
                "available_gb": 579.5,
                "mountpoint": "/srv/tank/media",
                "type": "Filesystem",
                "compression": "lz4",
                "encrypted": False,
            },
            {
                "dataset_key": "tank/docs",
                "name": "docs",
                "path": "tank/docs",
                "pool": "tank",
                "used_gb": 12.3,
                "available_gb": 579.5,
                "mountpoint": "/srv/tank/docs",
                "type": "Filesystem",
                "compression": "lz4",
                "encrypted": False,
            },
        ],
        "raid": [
            {
                "device": "md0",
                "disk_key": "md0",
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

    # Mirror __init__.py's _async_register_hierarchy_devices() so entity.py's
    # via_device_id lookups resolve against real device registry ids, the
    # same way production setup guarantees them (Issue #83).
    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        **get_hub_device_info(coordinator),
    )
    coordinator.hub_device_id = hub_device.id
    for project in sample_data.get("compose_projects", []):
        project_key = str(project.get("project_key") or project.get("name") or "")
        if not project_key:
            continue
        project_device = device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            **get_compose_project_device_info(coordinator, project),
        )
        coordinator.project_device_ids[project_key] = project_device.id

    return coordinator

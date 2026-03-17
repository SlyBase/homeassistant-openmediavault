"""Tests for OMV device info helpers."""

from __future__ import annotations

from custom_components.omv.const import DOMAIN
from custom_components.omv.entity import (
    get_compose_project_device_info,
    get_container_device_info,
    get_disk_device_info,
)


def test_physical_disk_device_info_uses_vendor_model_and_storage_label(coordinator) -> None:
    """Test physical disks get readable names and keep vendor/model metadata."""
    disk = coordinator.data["disk"][0]

    info = get_disk_device_info(coordinator, disk)

    assert info["name"] == "ATA Disk Model (data) [sda]"
    assert info["manufacturer"] == "ATA"
    assert info["model"] == "Disk Model"
    assert info["hw_version"] == "ABC123"


def test_logical_raid_device_info_uses_raid_name_and_omv_manufacturer(coordinator) -> None:
    """Test md-backed logical devices are shown as RAID devices."""
    disk = {
        "disk_key": "md127",
        "devicename": "md127",
        "canonicaldevicefile": "/dev/md127",
        "devicefile": "/dev/md127",
        "size": "200.0 GB",
        "vendor": "unknown",
        "model": "Linux MD RAID",
        "serialnumber": "unknown",
        "israid": True,
        "is_logical": True,
        "raid_level": "raid5",
        "storage_source": "zfs",
        "storage_label": "bigdata",
    }

    info = get_disk_device_info(coordinator, disk)

    assert info["name"] == "RAID md127 (bigdata)"
    assert info["manufacturer"] == "OpenMediaVault"
    assert info["model"] == "Linux MD RAID backed by ZFS (raid5)"
    assert info["hw_version"] == "md127"


def test_disk_device_info_deduplicates_vendor_and_ignores_partition_style_label(
    coordinator,
) -> None:
    """Test vendor/model duplication and partition labels are cleaned up."""
    disk = {
        "disk_key": "sda",
        "devicename": "sda",
        "canonicaldevicefile": "/dev/sda",
        "devicefile": "/dev/sda",
        "size": "2000.4 GB",
        "vendor": "QEMU",
        "model": "QEMU HARDDISK",
        "serialnumber": "QEMU-1",
        "israid": False,
        "is_logical": False,
        "storage_label": "sda1",
    }

    info = get_disk_device_info(coordinator, disk)

    assert info["name"] == "QEMU HARDDISK [sda]"
    assert info["manufacturer"] == "QEMU"
    assert info["model"] == "QEMU HARDDISK"


def test_compose_project_device_info_uses_compose_metadata(coordinator) -> None:
    """Test compose projects become dedicated parent devices."""
    project = coordinator.data["compose_projects"][0]

    info = get_compose_project_device_info(coordinator, project)

    assert info["name"] == "Compose paperless"
    assert info["manufacturer"] == "Docker Compose"
    assert info["model"] == "Compose Project"
    assert "serial_number" not in info


def test_container_device_info_uses_name_image_and_project_parent(coordinator) -> None:
    """Test containers become child devices below the compose project."""
    container = coordinator.data["compose"][0]

    info = get_container_device_info(coordinator, container)

    assert info["name"] == "Container paperless-app"
    assert info["manufacturer"] == "Docker"
    assert info["model"] == "ghcr.io/paperless-ngx/paperless-ngx:latest"
    assert info["sw_version"] == "2.15.3"
    assert info["via_device"] == (
        DOMAIN,
        f"{coordinator.config_entry.entry_id}:compose_project:paperless",
    )
    assert "serial_number" not in info


def test_volume_bound_device_info_uses_container_name_not_volume_name(coordinator) -> None:
    """Test volume-backed sensors keep the real container device name."""
    volume = next(
        item
        for item in coordinator.data["compose_volumes"]
        if item["volume_key"] == "ctr-vaultwarden:vaultwarden_data"
    )

    info = get_container_device_info(coordinator, volume)

    assert info["name"] == "Container vaultwarden"
    assert info["sw_version"] == "1.33.2"

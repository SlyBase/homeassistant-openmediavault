"""Tests for OMV device info helpers."""

from __future__ import annotations

from custom_components.omv.const import DOMAIN
from custom_components.omv.entity import (
    get_compose_project_device_info,
    get_container_device_info,
    get_disk_device_info,
    get_filesystem_device_identifier,
    get_filesystem_device_info,
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
        item for item in coordinator.data["compose_volumes"] if item["volume_key"] == "ctr-vaultwarden:vaultwarden_data"
    )

    info = get_container_device_info(coordinator, volume)

    assert info["name"] == "Container vaultwarden"
    assert info["sw_version"] == "1.33.2"


def test_filesystem_device_identifier_uses_filesystem_namespace(coordinator) -> None:
    """Filesystem device identifier must use the filesystem namespace."""
    result = get_filesystem_device_identifier(coordinator, "uuid-abc-123")
    assert result == (DOMAIN, f"{coordinator.config_entry.entry_id}:filesystem:uuid-abc-123")


def test_filesystem_without_disk_gets_standalone_device(coordinator) -> None:
    """Filesystem without a parent disk must get its own device, not the hub."""
    filesystem = {
        "uuid": "mergerfs-uuid-123",
        "label": "mergerfs_test",
        "type": "fuse.mergerfs",
        "disk_key": None,
        "mountdir": "/srv/mergerfs",
    }
    info = get_filesystem_device_info(coordinator, filesystem)

    entry_id = coordinator.config_entry.entry_id
    assert (DOMAIN, f"{entry_id}:filesystem:mergerfs-uuid-123") in info["identifiers"]
    assert (DOMAIN, entry_id) not in info["identifiers"]
    assert info["name"] == "Mergerfs (mergerfs_test)"
    assert info["model"] == "fuse.mergerfs"
    assert info["manufacturer"] == "OpenMediaVault"
    assert info["via_device"] == (DOMAIN, entry_id)


def test_filesystem_without_disk_uses_mountdir_when_no_label(coordinator) -> None:
    """Filesystem without label should fall back to mountdir for display name."""
    filesystem = {
        "uuid": "nfs-uuid-456",
        "label": "",
        "type": "nfs",
        "disk_key": None,
        "mountdir": "/srv/nfs-share",
    }
    info = get_filesystem_device_info(coordinator, filesystem)

    assert info["name"] == "NFS (/srv/nfs-share)"
    assert info["model"] == "nfs"


def test_filesystem_with_disk_still_maps_to_disk_device(coordinator) -> None:
    """Filesystem with a valid disk_key must still map to the disk device."""
    disk = coordinator.data["disk"][0]
    disk_key = str(disk.get("disk_key") or disk.get("devicename") or "")
    filesystem = {
        "uuid": "ext4-uuid-789",
        "label": "data",
        "type": "ext4",
        "disk_key": disk_key,
        "mountdir": "/srv/data",
    }
    info = get_filesystem_device_info(coordinator, filesystem)

    entry_id = coordinator.config_entry.entry_id
    assert (DOMAIN, f"{entry_id}:disk:{disk_key}") in info["identifiers"]
    assert "filesystem" not in str(info["identifiers"])


def test_filesystem_without_uuid_falls_back_to_hub(coordinator) -> None:
    """Filesystem without UUID and without disk_key must fall back to hub device."""
    filesystem = {
        "uuid": "",
        "label": "broken",
        "type": "tmpfs",
        "disk_key": None,
        "mountdir": "/tmp",
    }
    info = get_filesystem_device_info(coordinator, filesystem)

    entry_id = coordinator.config_entry.entry_id
    assert (DOMAIN, entry_id) in info["identifiers"]

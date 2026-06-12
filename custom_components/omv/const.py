"""Constants for the OpenMediaVault integration."""

from homeassistant.const import Platform

DOMAIN = "omv"

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.UPDATE,
]

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True


# Optionsschlüssel für auswählbare Ressourcen
CONF_SCAN_INTERVAL = "scan_interval"
CONF_REBOOT_REPAIR_DISABLED = "reboot_repair_disabled"
CONF_SELECTED_DISKS = "selected_disks"
CONF_SELECTED_FILESYSTEMS = "selected_filesystems"
CONF_SELECTED_SERVICES = "selected_services"
CONF_SELECTED_NETWORK_INTERFACES = "selected_network_interfaces"
CONF_SELECTED_RAIDS = "selected_raids"
CONF_SELECTED_ZFS_POOLS = "selected_zfs_pools"
CONF_SELECTED_CONTAINERS = "selected_containers"
CONF_SELECTED_COMPOSE_PROJECTS = "selected_compose_projects"
CONF_SELECTED_VMS = "selected_vms"

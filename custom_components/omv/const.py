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


# Optionales TOTP-Secret (entry.data) für automatische Re-Logins bei 2FA (Issue #55)
CONF_TOTP_SECRET = "totp_secret"

# Optionsschlüssel für auswählbare Ressourcen
CONF_SCAN_INTERVAL = "scan_interval"
CONF_REBOOT_REPAIR_DISABLED = "reboot_repair_disabled"
# SMART polling controls (Issue #41) — decouple SMART from the scan interval so
# disks can spin down. CONF_SMART_INTERVAL defaults to the scan interval.
CONF_SMART_INTERVAL = "smart_interval"
CONF_SMART_POLLING_DISABLED = "smart_polling_disabled"
CONF_SELECTED_DISKS = "selected_disks"
CONF_SELECTED_FILESYSTEMS = "selected_filesystems"
CONF_SELECTED_SERVICES = "selected_services"
CONF_SELECTED_NETWORK_INTERFACES = "selected_network_interfaces"
CONF_SELECTED_RAIDS = "selected_raids"
CONF_SELECTED_ZFS_POOLS = "selected_zfs_pools"
CONF_SELECTED_CONTAINERS = "selected_containers"
CONF_SELECTED_COMPOSE_PROJECTS = "selected_compose_projects"
CONF_SELECTED_VMS = "selected_vms"
# Opt-in (kein Datenfilter): Cron-Buttons nur für explizit ausgewählte Jobs
CONF_SELECTED_CRON_JOBS = "selected_cron_jobs"

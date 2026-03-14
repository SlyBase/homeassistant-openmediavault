"""Constants for the OpenMediaVault integration."""

from homeassistant.const import Platform

DOMAIN = "omv"

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True

CONF_SCAN_INTERVAL = "scan_interval"
CONF_SMART_DISABLED = "smart_disabled"
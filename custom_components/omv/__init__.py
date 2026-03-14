"""The OpenMediaVault integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SMART_DISABLED,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    PLATFORMS,
)
from .coordinator import OMVDataUpdateCoordinator
from .exceptions import OMVAuthError, OMVConnectionError
from .omv_api import OMVAPI

type OMVConfigEntry = ConfigEntry[OMVDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: OMVConfigEntry) -> bool:
    """Set up OMV from a config entry."""
    api = OMVAPI(
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        ssl=entry.data.get(CONF_SSL, DEFAULT_SSL),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        source="setup_entry",
    )

    try:
        system_info = await api.async_connect()
    except OMVAuthError as err:
        await api.async_close()
        raise ConfigEntryAuthFailed("OMV authentication failed") from err
    except OMVConnectionError as err:
        await api.async_close()
        raise ConfigEntryNotReady("Cannot connect to OMV") from err

    coordinator = OMVDataUpdateCoordinator(
        hass,
        entry,
        api,
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        smart_disabled=entry.options.get(CONF_SMART_DISABLED, False),
    )
    await coordinator.async_init(system_info)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OMVConfigEntry) -> bool:
    """Unload the OMV config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.api.async_close()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: OMVConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

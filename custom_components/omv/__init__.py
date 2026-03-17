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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SMART_DISABLED,
    CONF_VIRTUAL_PASSTHROUGH,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import OMVDataUpdateCoordinator
from .exceptions import OMVAuthError, OMVConnectionError
from .omv_api import OMVAPI

type OMVConfigEntry = ConfigEntry[OMVDataUpdateCoordinator]


def _register_registry_cleanup_listener(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> None:
    """Clean up stale dynamic entities after coordinator refreshes."""
    cleanup_task = None

    @callback
    def _schedule_cleanup() -> None:
        nonlocal cleanup_task
        if cleanup_task is not None and not cleanup_task.done():
            return
        cleanup_task = hass.async_create_task(
            _async_cleanup_stale_registry_entries(hass, entry, coordinator)
        )

    entry.async_on_unload(coordinator.async_add_listener(_schedule_cleanup))


async def _async_cleanup_stale_registry_entries(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> None:
    """Remove stale entity and disk device registry entries after a reload."""
    from .sensor import get_expected_sensor_registry_state
    from .binary_sensor import get_expected_binary_sensor_unique_ids
    from .button import get_expected_button_unique_ids

    expected_entity_unique_ids, expected_device_identifiers = get_expected_sensor_registry_state(
        coordinator
    )
    expected_entity_unique_ids.update(get_expected_binary_sensor_unique_ids(coordinator))
    expected_entity_unique_ids.update(get_expected_button_unique_ids(entry, coordinator))

    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if registry_entry.unique_id not in expected_entity_unique_ids:
            entity_registry.async_remove(registry_entry.entity_id)

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        resource_identifiers = {
            identifier
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN
            and identifier[1].startswith(f"{entry.entry_id}:")
        }
        if resource_identifiers and not (resource_identifiers & expected_device_identifiers):
            device_registry.async_remove_device(device_entry.id)


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
        virtual_passthrough=entry.options.get(CONF_VIRTUAL_PASSTHROUGH, False),
    )
    await coordinator.async_init(system_info)
    await coordinator.async_config_entry_first_refresh()
    await _async_cleanup_stale_registry_entries(hass, entry, coordinator)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_registry_cleanup_listener(hass, entry, coordinator)
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

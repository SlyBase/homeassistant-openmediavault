"""The OpenMediaVault integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from . import session_handoff
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOTP_SECRET,
    CONF_UPDATE_TRACKING_DISABLED,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LOGIN_COOKIE_STORAGE_KEY,
    LOGIN_COOKIE_STORAGE_VERSION,
    PLATFORMS,
)
from .coordinator import OMVDataUpdateCoordinator
from .entity import get_compose_project_device_info, get_hub_device_info
from .exceptions import OMVApiError, OMVAuthError, OMVConnectionError
from .omv_api import OMVAPI
from .repairs import async_delete_reboot_repair_issue, async_sync_reboot_repair_issue
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

type OMVConfigEntry = ConfigEntry[OMVDataUpdateCoordinator]


def _platforms_for_entry(entry: OMVConfigEntry) -> list[Platform]:
    """Return the platforms to forward/unload for a config entry.

    Excludes ``Platform.UPDATE`` when ``CONF_UPDATE_TRACKING_DISABLED`` is
    set (Issue #66), so no HA `update` entity is created for OMV package
    updates. The pending-update-count sensor and its underlying hwinfo data
    are unaffected.

    Args:
        entry: The config entry.

    Returns:
        The list of platforms to set up or tear down for this entry.
    """
    if entry.options.get(CONF_UPDATE_TRACKING_DISABLED, False):
        return [platform for platform in PLATFORMS if platform != Platform.UPDATE]
    return PLATFORMS


def _login_cookie_store(hass: HomeAssistant, entry: OMVConfigEntry) -> Store:
    """Return the per-entry Store for the OMV login-notification cookie name.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry the cookie belongs to.

    Returns:
        A :class:`Store` keyed by the entry id (Issue #62).
    """
    return Store(
        hass,
        LOGIN_COOKIE_STORAGE_VERSION,
        f"{DOMAIN}.{LOGIN_COOKIE_STORAGE_KEY}.{entry.entry_id}",
    )


async def _async_persist_login_cookie(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    api: OMVAPI,
) -> None:
    """Persist the OMV login-notification dedup cookie name for reuse.

    Stores the ``OPENMEDIAVAULT-LOGIN-*`` cookie name OMV issued on first
    login so a later HA restart can replay it and OMV does not re-send the
    login-notification email (Issue #62). No-op when no cookie has been
    captured yet or the stored value is already up to date.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        api: The live OMV API client.
    """
    name = api.get_login_cookie_name()
    if not isinstance(name, str) or not name:
        return
    store = _login_cookie_store(hass, entry)
    stored = await store.async_load()
    if isinstance(stored, dict) and stored.get("cookie_name") == name:
        return
    await store.async_save({"cookie_name": name})


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
        cleanup_task = hass.async_create_task(_async_cleanup_stale_registry_entries(hass, entry, coordinator))

    entry.async_on_unload(coordinator.async_add_listener(_schedule_cleanup))


def _register_reboot_repair_listener(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> None:
    """Keep the reboot repair issue synchronized with coordinator data."""

    @callback
    def _sync_reboot_repair() -> None:
        async_sync_reboot_repair_issue(hass, entry)

    _sync_reboot_repair()
    entry.async_on_unload(coordinator.async_add_listener(_sync_reboot_repair))


async def _async_register_hierarchy_devices(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> None:
    """Pre-register the hub and compose-project devices, caching their registry ids.

    ``DeviceInfo.via_device_id`` (Issue #83) needs the *parent* device to
    already exist in the registry at the moment a child entity's
    ``DeviceInfo`` is built, unlike the deprecated ``via_device`` identifier
    tuple which HA resolved lazily. Registering the hub device and every
    compose-project device here, before ``async_forward_entry_setups`` sets
    up any platform, guarantees that invariant for every disk/filesystem/
    container/VM device created afterwards.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        coordinator: The coordinator, already refreshed with current data.
    """
    device_registry = dr.async_get(hass)

    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **get_hub_device_info(coordinator),
    )
    coordinator.hub_device_id = hub_device.id

    for project in coordinator.data.get("compose_projects", []):
        project_key = str(project.get("project_key") or project.get("name") or "")
        if not project_key:
            continue
        project_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **get_compose_project_device_info(coordinator, project),
        )
        coordinator.project_device_ids[project_key] = project_device.id


async def _async_migrate_container_registry_keys(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> None:
    """Rename container devices/entities from the old id-keyed scheme to the name-keyed one.

    ``container_key`` used to default to the Docker runtime container id,
    which Docker reassigns on every recreate (e.g. an image pull followed by
    ``compose down && up``). That silently dropped the container from
    ``selected_containers`` and reset any entity customization (name,
    enabled state) on every image update (Issue #71). ``container_key`` is
    now derived from the stable compose/container name instead, but existing
    installs still have their container devices/entities registered under
    the old id-keyed identifiers. Rename those in place — matched via the
    device's stored integration name (``Container <name>``), which was
    always the real container name even under the old scheme — instead of
    letting :func:`_async_cleanup_stale_registry_entries` drop them and
    recreate fresh ones.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        coordinator: The coordinator, already refreshed with current data.
    """
    current_keys = {str(container.get("container_key") or "") for container in coordinator.data.get("compose", [])}
    current_keys.discard("")
    if not current_keys:
        return

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    prefix = f"{entry.entry_id}:container:"

    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        old_key = next(
            (
                identifier[1].removeprefix(prefix)
                for identifier in device_entry.identifiers
                if identifier[0] == DOMAIN and identifier[1].startswith(prefix)
            ),
            None,
        )
        if not old_key or old_key in current_keys:
            continue

        new_key = (device_entry.name or "").removeprefix("Container ").strip()
        if not new_key or new_key not in current_keys:
            continue

        new_identifiers = {
            (DOMAIN, f"{prefix}{new_key}") if identifier == (DOMAIN, f"{prefix}{old_key}") else identifier
            for identifier in device_entry.identifiers
        }
        device_registry.async_update_device(device_entry.id, new_identifiers=new_identifiers)

        for registry_entry in er.async_entries_for_device(
            entity_registry, device_entry.id, include_disabled_entities=True
        ):
            if not registry_entry.unique_id.endswith(f"-{old_key}"):
                continue
            new_unique_id = f"{registry_entry.unique_id[: -len(old_key)]}{new_key}"
            entity_registry.async_update_entity(registry_entry.entity_id, new_unique_id=new_unique_id)


async def _async_cleanup_stale_registry_entries(
    hass: HomeAssistant,
    entry: OMVConfigEntry,
    coordinator: OMVDataUpdateCoordinator,
) -> None:
    """Remove stale entity and disk device registry entries after a reload."""
    from .binary_sensor import get_expected_binary_sensor_unique_ids
    from .button import get_expected_button_unique_ids
    from .sensor import get_expected_sensor_registry_state
    from .switch import get_expected_switch_unique_ids
    from .update import get_expected_update_unique_ids

    expected_entity_unique_ids, expected_device_identifiers = get_expected_sensor_registry_state(coordinator)
    expected_entity_unique_ids.update(get_expected_binary_sensor_unique_ids(coordinator))
    expected_entity_unique_ids.update(get_expected_button_unique_ids(entry, coordinator))
    expected_entity_unique_ids.update(get_expected_switch_unique_ids(coordinator))
    expected_entity_unique_ids.update(get_expected_update_unique_ids(entry))

    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if registry_entry.unique_id not in expected_entity_unique_ids:
            entity_registry.async_remove(registry_entry.entity_id)

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        resource_identifiers = {
            identifier
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN and identifier[1].startswith(f"{entry.entry_id}:")
        }
        if resource_identifiers and not (resource_identifiers & expected_device_identifiers):
            device_registry.async_remove_device(device_entry.id)


async def async_setup_entry(hass: HomeAssistant, entry: OMVConfigEntry) -> bool:
    """Set up OMV from a config entry.

    If the config flow just authenticated this host (user/reconfigure/reauth),
    reuses that already-authenticated :class:`~custom_components.omv.omv_api.OMVAPI`
    session and its ``system_info`` via :mod:`custom_components.omv.session_handoff`
    instead of opening a brand new OMV login, since OMV challenges 2FA-enabled
    accounts fresh on every login and nobody is present to answer a second
    challenge triggered by the automatic post-flow reload.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry to set up.

    Returns:
        True once setup has completed successfully.

    Raises:
        ConfigEntryAuthFailed: If OMV authentication fails (no pending hand-off).
        ConfigEntryNotReady: If OMV cannot be reached (no pending hand-off).
    """
    handoff = session_handoff.pop(entry.unique_id)
    if handoff is not None:
        # Reuse the session the config flow (user/reconfigure/reauth) just
        # authenticated, instead of opening a brand new OMV login — OMV
        # always challenges 2FA accounts fresh, so a second login right
        # after the flow finished would fail with nobody there to answer it.
        api, system_info = handoff
    else:
        api = OMVAPI(
            host=entry.data[CONF_HOST],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            ssl=entry.data.get(CONF_SSL, DEFAULT_SSL),
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            source="setup_entry",
            totp_secret=entry.data.get(CONF_TOTP_SECRET),
        )

        # Replay a previously captured OMV login-notification dedup cookie so
        # the first login after an HA restart does not trigger another
        # security-notification email (Issue #62).
        stored_cookie = await _login_cookie_store(hass, entry).async_load()
        if isinstance(stored_cookie, dict) and stored_cookie.get("cookie_name"):
            api.set_login_cookie_name(stored_cookie["cookie_name"])

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
    )
    await coordinator.async_init(system_info)
    await coordinator.async_config_entry_first_refresh()
    await _async_persist_login_cookie(hass, entry, api)
    entry.runtime_data = coordinator
    await _async_register_hierarchy_devices(hass, entry, coordinator)
    await _async_migrate_container_registry_keys(hass, entry, coordinator)
    await _async_cleanup_stale_registry_entries(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, _platforms_for_entry(entry))
    _register_registry_cleanup_listener(hass, entry, coordinator)
    _register_reboot_repair_listener(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OMVConfigEntry) -> bool:
    """Unload the OMV config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _platforms_for_entry(entry))
    if unload_ok:
        async_delete_reboot_repair_issue(hass, entry.entry_id)
        # If _async_update_listener just stashed THIS live, already-authenticated
        # session for the immediate reload below, leave it open — closing it
        # here would force the reload's async_setup_entry into a brand new
        # OMV login, re-triggering a 2FA challenge for a session that was
        # still perfectly valid. A pending hand-off holding a DIFFERENT
        # instance (reauth/reconfigure just authenticated a new session)
        # means the old one is obsolete and must be closed, or it leaks.
        if not session_handoff.pending_api_is(entry.unique_id, entry.runtime_data.api):
            await entry.runtime_data.api.async_close()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: OMVConfigEntry) -> None:
    """Remove the persisted OMV login-notification cookie on entry deletion.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being removed.
    """
    await _login_cookie_store(hass, entry).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: OMVConfigEntry) -> None:
    """Reload the entry when options change, reusing the live OMV session.

    Options changes (scan interval, resource filters, feature flags) don't
    invalidate the OMV session, but a plain ``async_reload`` tears down the
    API client and forces a brand new ``Session.login`` on setup — which
    re-triggers a 2FA challenge for accounts with TOTP enabled, even though
    nobody changed any credentials. Hand off the still-authenticated API
    instance (refreshing ``system_info`` on it first) so the reload reuses
    it instead, exactly like the reconfigure/reauth flows already do.
    """
    coordinator = entry.runtime_data
    system_info: dict[str, Any] | None = None
    try:
        response = await coordinator.api.async_call("System", "getInformation")
        if isinstance(response, dict):
            system_info = response
    except (OMVApiError, OMVConnectionError) as err:
        _LOGGER.debug(
            "Could not refresh system_info for options-reload hand-off on %s: %s",
            entry.entry_id,
            err,
        )
    if entry.unique_id is not None and system_info is not None:
        session_handoff.store(entry.unique_id, coordinator.api, system_info)
    await hass.config_entries.async_reload(entry.entry_id)

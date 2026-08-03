"""Tests for OMV integration setup (custom_components/omv/__init__.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

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
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv import (
    _async_migrate_container_registry_keys,
    _async_persist_login_cookie,
    _login_cookie_store,
    session_handoff,
)
from custom_components.omv.const import CONF_SCAN_INTERVAL, DOMAIN
from custom_components.omv.coordinator import OMVDataUpdateCoordinator

ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_PORT: 80,
    CONF_SSL: False,
    CONF_VERIFY_SSL: True,
}


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=ENTRY_DATA,
    )


def _mock_handoff_api() -> AsyncMock:
    """Return a mock OMV API suitable for session-handoff tests.

    ``get_login_cookie_name`` is a synchronous stub returning ``None`` so the
    login-cookie persistence in ``async_setup_entry`` is a no-op and does not
    try to serialise an ``AsyncMock`` coroutine (Issue #62).
    """
    api = AsyncMock()
    api.get_login_cookie_name = MagicMock(return_value=None)
    return api


@pytest.mark.asyncio
async def test_persist_login_cookie_writes_captured_name(hass) -> None:
    """A captured OMV login-dedup cookie name is written to the entry store (Issue #62)."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    api = _mock_handoff_api()
    api.get_login_cookie_name = MagicMock(return_value="OPENMEDIAVAULT-LOGIN-xyz")

    await _async_persist_login_cookie(hass, entry, api)

    stored = await _login_cookie_store(hass, entry).async_load()
    assert stored == {"cookie_name": "OPENMEDIAVAULT-LOGIN-xyz"}


@pytest.mark.asyncio
async def test_persist_login_cookie_noop_without_name(hass) -> None:
    """Nothing is stored when no OMV login-dedup cookie has been captured (Issue #62)."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    api = _mock_handoff_api()  # get_login_cookie_name() -> None

    await _async_persist_login_cookie(hass, entry, api)

    assert await _login_cookie_store(hass, entry).async_load() is None


@pytest.mark.asyncio
async def test_async_setup_entry_reuses_handed_off_session(hass) -> None:
    """A session stashed by the config flow is reused instead of a fresh OMV login."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    handed_off_api = _mock_handoff_api()
    system_info = {"hostname": "nas", "version": "8.1.2-1"}
    session_handoff.store("nas", handed_off_api, system_info)

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(side_effect=AssertionError("should not open a new OMV login")),
        ),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator.async_init",
            new=AsyncMock(),
        ) as mock_async_init,
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    mock_async_init.assert_awaited_once_with(system_info)
    assert entry.runtime_data.api is handed_off_api
    # The stashed session was consumed and is gone from the registry.
    assert session_handoff.pop("nas") is None


@pytest.mark.asyncio
async def test_async_setup_entry_connects_fresh_without_handoff(hass) -> None:
    """Without a stashed session, setup opens a normal OMV login as before."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    system_info = {"hostname": "nas", "version": "8.1.2-1"}

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(return_value=system_info),
        ) as mock_connect,
        patch("custom_components.omv.OMVAPI.async_close", new=AsyncMock()),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator.async_init",
            new=AsyncMock(),
        ) as mock_async_init,
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    mock_connect.assert_awaited_once()
    mock_async_init.assert_awaited_once_with(system_info)


@pytest.mark.asyncio
async def test_async_setup_entry_multi_instance_handoffs_stay_separate(hass) -> None:
    """Two entries with distinct unique_ids each pop only their own hand-off."""
    entry1 = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas1)",
        unique_id="nas1",
        data={**ENTRY_DATA, CONF_HOST: "192.0.2.10"},
    )
    entry2 = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas2)",
        unique_id="nas2",
        data={**ENTRY_DATA, CONF_HOST: "192.0.2.11"},
    )
    entry1.add_to_hass(hass)
    entry2.add_to_hass(hass)

    api1 = _mock_handoff_api()
    api2 = _mock_handoff_api()
    session_handoff.store("nas1", api1, {"hostname": "nas1", "version": "8.1.2-1"})
    session_handoff.store("nas2", api2, {"hostname": "nas2", "version": "8.1.2-1"})

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(side_effect=AssertionError("should not open a new OMV login")),
        ),
        patch("custom_components.omv.OMVDataUpdateCoordinator.async_init", new=AsyncMock()),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        # Loading the component sets up every entry of the domain.
        assert await hass.config_entries.async_setup(entry1.entry_id) is True
        await hass.async_block_till_done()

    assert entry1.runtime_data.api is api1
    assert entry2.runtime_data.api is api2
    assert session_handoff.pop("nas1") is None
    assert session_handoff.pop("nas2") is None


@pytest.mark.asyncio
async def test_unload_closes_old_api_when_handoff_holds_new_instance(hass) -> None:
    """After reauth/reconfigure the hand-off holds a NEW api — unload must close the old one."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    old_api = _mock_handoff_api()
    session_handoff.store("nas", old_api, {"hostname": "nas", "version": "8.1.2-1"})

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(side_effect=AssertionError("should not open a new OMV login")),
        ),
        patch("custom_components.omv.OMVDataUpdateCoordinator.async_init", new=AsyncMock()),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        assert entry.runtime_data.api is old_api

        # Simulate a reauth flow that just stashed a brand-new authenticated
        # session for the reload that follows this unload.
        new_api = AsyncMock()
        session_handoff.store("nas", new_api, {"hostname": "nas", "version": "8.1.2-1"})

        assert await hass.config_entries.async_unload(entry.entry_id) is True

    # The obsolete session must be closed (previously it leaked), while the
    # handed-off replacement stays available for the reload.
    old_api.async_close.assert_awaited_once()
    new_api.async_close.assert_not_awaited()
    popped = session_handoff.pop("nas")
    assert popped is not None and popped[0] is new_api


@pytest.mark.asyncio
async def test_unload_keeps_api_open_when_handoff_holds_same_instance(hass) -> None:
    """The options-reload hand-off holds the SAME api — unload must not close it."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    api = _mock_handoff_api()
    session_handoff.store("nas", api, {"hostname": "nas", "version": "8.1.2-1"})

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(side_effect=AssertionError("should not open a new OMV login")),
        ),
        patch("custom_components.omv.OMVDataUpdateCoordinator.async_init", new=AsyncMock()),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        assert entry.runtime_data.api is api

        # Simulate _async_update_listener stashing the still-live session for
        # the immediate options reload.
        session_handoff.store("nas", api, {"hostname": "nas", "version": "8.1.2-1"})

        assert await hass.config_entries.async_unload(entry.entry_id) is True

    api.async_close.assert_not_awaited()
    popped = session_handoff.pop("nas")
    assert popped is not None and popped[0] is api


@pytest.mark.asyncio
async def test_options_update_reuses_live_session_without_relogin(hass) -> None:
    """Changing an option must not force a fresh OMV login (and thus a 2FA challenge).

    Regression test: previously any options-flow change (scan interval,
    resource filters, ...) triggered a plain ``async_reload``, which tore
    down the still-valid OMV session and forced ``async_connect`` to run a
    brand new ``Session.login`` on setup — re-challenging 2FA-enabled
    accounts for no reason.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    system_info = {"hostname": "nas", "version": "8.1.2-1"}

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(return_value=system_info),
        ) as mock_connect,
        patch("custom_components.omv.OMVAPI.async_close", new=AsyncMock()) as mock_close,
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator.async_init",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.omv.OMVDataUpdateCoordinator._async_update_data",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        mock_connect.assert_awaited_once()
        mock_close.assert_not_awaited()

        live_api = entry.runtime_data.api
        live_api.async_call = AsyncMock(return_value=system_info)

        hass.config_entries.async_update_entry(entry, options={CONF_SCAN_INTERVAL: 120})
        await hass.async_block_till_done()

    # The reload must reuse the same, still-authenticated API instance
    # instead of opening a second OMV login.
    live_api.async_call.assert_awaited_once_with("System", "getInformation")
    mock_connect.assert_awaited_once()
    mock_close.assert_not_awaited()
    assert entry.runtime_data.api is live_api


@pytest.mark.asyncio
async def test_migrate_container_registry_keys_renames_old_id_based_entries(hass) -> None:
    """Existing container devices/entities move from the old id-keyed scheme to the name-keyed one.

    Regression test for Issue #71: ``container_key`` used to default to the
    ephemeral Docker runtime id, so recreating a container (image pull +
    ``compose down && up``) changed its unique_id/device identifier on every
    update, silently dropping it from ``selected_containers`` and resetting
    any entity customization. The migration must retarget the *existing*
    registry entries onto the new name-keyed identifiers, preserving user
    customizations, instead of leaving them for the stale-cleanup pass to
    drop and recreate from scratch.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    old_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}:container:abc123")},
        name="Container paperless-app",
    )
    switch_entry = entity_registry.async_get_or_create(
        DOMAIN,
        "switch",
        f"{entry.entry_id}-container-abc123",
        config_entry=entry,
        device_id=old_device.id,
        original_name="paperless-app",
    )
    entity_registry.async_update_entity(switch_entry.entity_id, name="My Paperless")
    button_entry = entity_registry.async_get_or_create(
        DOMAIN,
        "button",
        f"{entry.entry_id}-container_restart-abc123",
        config_entry=entry,
        device_id=old_device.id,
        original_name="paperless-app restart",
    )

    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, entry, api, scan_interval=60)
    coordinator.data = {
        "compose": [{"container_key": "paperless-app", "container_id": "def456", "name": "paperless-app"}],
    }

    await _async_migrate_container_registry_keys(hass, entry, coordinator)

    migrated_device = device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}:container:paperless-app")})
    assert migrated_device is not None
    assert migrated_device.id == old_device.id
    assert device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}:container:abc123")}) is None

    switch_entity_id = entity_registry.async_get_entity_id(
        DOMAIN, "switch", f"{entry.entry_id}-container-paperless-app"
    )
    assert switch_entity_id == switch_entry.entity_id
    assert entity_registry.async_get(switch_entity_id).name == "My Paperless"

    button_entity_id = entity_registry.async_get_entity_id(
        DOMAIN, "button", f"{entry.entry_id}-container_restart-paperless-app"
    )
    assert button_entity_id == button_entry.entity_id


@pytest.mark.asyncio
async def test_migrate_container_registry_keys_ignores_removed_containers(hass) -> None:
    """A stale device whose container no longer exists at all must be left alone.

    Only containers that are still present (recreated under the same name)
    are migrated; containers genuinely removed from OMV must still be
    cleaned up by ``_async_cleanup_stale_registry_entries`` as before.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}:container:abc123")},
        name="Container removed-app",
    )

    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    coordinator = OMVDataUpdateCoordinator(hass, entry, api, scan_interval=60)
    coordinator.data = {
        "compose": [{"container_key": "paperless-app", "container_id": "def456", "name": "paperless-app"}],
    }

    await _async_migrate_container_registry_keys(hass, entry, coordinator)

    unchanged_device = device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}:container:abc123")})
    assert unchanged_device is not None
    assert unchanged_device.id == old_device.id

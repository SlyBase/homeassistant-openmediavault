"""Tests for OMV repairs."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir

from custom_components.omv.const import DOMAIN
from custom_components.omv.repairs import (
    OMVRebootRepairFlow,
    async_create_fix_flow,
    async_sync_reboot_repair_issue,
    get_reboot_required_issue_id,
)


def _get_issue(hass, entry_id: str):
    """Return the reboot repair issue for the config entry."""
    return ir.async_get(hass).async_get_issue(DOMAIN, get_reboot_required_issue_id(entry_id))


def _build_flow(hass, config_entry) -> OMVRebootRepairFlow:
    """Create a repair flow instance that can run standalone in tests."""
    flow = OMVRebootRepairFlow(config_entry)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "test-flow"
    flow.context = {}
    return flow


def test_async_sync_reboot_repair_issue_creates_issue_when_only_reboot_is_pending(
    hass,
    coordinator,
    config_entry,
) -> None:
    """Test the reboot repair is created after updates are installed."""
    coordinator.data["hwinfo"]["availablePkgUpdates"] = 0
    coordinator.data["hwinfo"]["rebootRequired"] = True
    coordinator.data["hwinfo"]["configDirty"] = False

    async_sync_reboot_repair_issue(hass, config_entry)

    issue = _get_issue(hass, config_entry.entry_id)
    assert issue is not None
    assert issue.data == {"entry_id": config_entry.entry_id}
    assert issue.translation_key == "reboot_required"
    assert issue.translation_placeholders == {"title": config_entry.title}
    assert issue.severity == ir.IssueSeverity.WARNING


@pytest.mark.parametrize(
    ("pending_updates", "reboot_required", "config_dirty"),
    [
        (1, True, False),
        (0, False, False),
        (0, True, True),
    ],
)
def test_async_sync_reboot_repair_issue_deletes_issue_when_not_actionable(
    hass,
    coordinator,
    config_entry,
    pending_updates: int,
    reboot_required: bool,
    config_dirty: bool,
) -> None:
    """Test the reboot repair disappears when reboot is no longer actionable."""
    coordinator.data["hwinfo"]["availablePkgUpdates"] = 0
    coordinator.data["hwinfo"]["rebootRequired"] = True
    coordinator.data["hwinfo"]["configDirty"] = False
    async_sync_reboot_repair_issue(hass, config_entry)

    coordinator.data["hwinfo"]["availablePkgUpdates"] = pending_updates
    coordinator.data["hwinfo"]["rebootRequired"] = reboot_required
    coordinator.data["hwinfo"]["configDirty"] = config_dirty
    if config_dirty:
        coordinator.data["hwinfo"]["dirtyModules"] = ["nginx"]

    async_sync_reboot_repair_issue(hass, config_entry)

    assert _get_issue(hass, config_entry.entry_id) is None


@pytest.mark.asyncio
async def test_async_create_fix_flow_returns_reboot_flow(hass, config_entry) -> None:
    """Test the repair platform returns the OMV reboot flow."""
    config_entry.add_to_hass(hass)

    flow = await async_create_fix_flow(
        hass,
        get_reboot_required_issue_id(config_entry.entry_id),
        {"entry_id": config_entry.entry_id},
    )

    assert isinstance(flow, OMVRebootRepairFlow)


@pytest.mark.asyncio
async def test_reboot_repair_flow_reboots_host_and_clears_flag(
    hass,
    coordinator,
    config_entry,
) -> None:
    """Test confirming the repair reboots OMV and clears the reboot flag."""
    coordinator.data["hwinfo"]["availablePkgUpdates"] = 0
    coordinator.data["hwinfo"]["rebootRequired"] = True
    coordinator.data["hwinfo"]["configDirty"] = False
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_update_listeners = Mock()
    coordinator.api.async_call = AsyncMock()
    flow = _build_flow(hass, config_entry)

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator.api.async_call.assert_awaited_once_with("System", "reboot")
    assert coordinator.data["hwinfo"]["rebootRequired"] is False
    coordinator.async_update_listeners.assert_called_once_with()


@pytest.mark.asyncio
async def test_reboot_repair_flow_aborts_when_config_becomes_dirty(
    hass,
    coordinator,
    config_entry,
) -> None:
    """Test the repair aborts when OMV still has pending config changes."""
    coordinator.data["hwinfo"]["availablePkgUpdates"] = 0
    coordinator.data["hwinfo"]["rebootRequired"] = True
    coordinator.data["hwinfo"]["configDirty"] = False
    async_sync_reboot_repair_issue(hass, config_entry)

    coordinator.data["hwinfo"]["configDirty"] = True
    coordinator.data["hwinfo"]["dirtyModules"] = ["nginx", "samba"]
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api.async_call = AsyncMock()
    flow = _build_flow(hass, config_entry)

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "config_dirty"
    assert result["description_placeholders"] == {"modules": "nginx, samba"}
    coordinator.api.async_call.assert_not_awaited()
    assert _get_issue(hass, config_entry.entry_id) is None

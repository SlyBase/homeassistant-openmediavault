"""Tests for OMV button entities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.omv.button import OMVRebootButton, OMVShutdownButton, async_setup_entry


@pytest.mark.asyncio
async def test_async_setup_entry_adds_buttons(coordinator, config_entry) -> None:
    """Test button platform setup."""
    added = []

    def add_entities(entities):
        added.extend(entities)

    await async_setup_entry(coordinator.hass, config_entry, add_entities)

    assert len(added) == 2


@pytest.mark.asyncio
async def test_reboot_button_calls_reboot(coordinator) -> None:
    """Test reboot button RPC call."""
    coordinator.api.async_call = AsyncMock()
    button = OMVRebootButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "reboot")


@pytest.mark.asyncio
async def test_shutdown_button_calls_shutdown(coordinator) -> None:
    """Test shutdown button RPC call."""
    coordinator.api.async_call = AsyncMock()
    button = OMVShutdownButton(coordinator)

    await button.async_press()

    coordinator.api.async_call.assert_awaited_once_with("System", "shutdown")
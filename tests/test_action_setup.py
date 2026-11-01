"""Tests for service registration in async_setup (quality scale: action-setup)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.omv import async_setup
from custom_components.omv.const import DOMAIN
from custom_components.omv.services import (
    SERVICE_APPLY_CONFIG,
    SERVICE_COMPOSE_COMMAND,
    SERVICE_CONTAINER_COMMAND,
    SERVICE_RUN_RSYNC_JOB,
)

ALL_SERVICES = (
    SERVICE_APPLY_CONFIG,
    SERVICE_COMPOSE_COMMAND,
    SERVICE_CONTAINER_COMMAND,
    SERVICE_RUN_RSYNC_JOB,
)


@pytest.mark.asyncio
async def test_async_setup_registers_all_services(hass: HomeAssistant) -> None:
    """async_setup registers every OMV service without a config entry."""
    await async_setup(hass, {})
    for service in ALL_SERVICES:
        assert hass.services.has_service(DOMAIN, service), f"missing service {service}"


@pytest.mark.asyncio
async def test_services_exist_without_loaded_entry(hass: HomeAssistant) -> None:
    """Services are available as soon as the integration loads, before any entry."""
    await async_setup(hass, {})
    for service in ALL_SERVICES:
        assert hass.services.has_service(DOMAIN, service)
    # No config entries exist at all.
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.asyncio
async def test_service_call_without_loaded_entry_raises_validation_error(
    hass: HomeAssistant,
) -> None:
    """A service call with no loaded entry raises ServiceValidationError."""
    from homeassistant.exceptions import ServiceValidationError

    await async_setup(hass, {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CONTAINER_COMMAND,
            {"container": "web", "command": "start"},
            blocking=True,
        )

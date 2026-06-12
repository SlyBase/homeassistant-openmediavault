"""Tests for the OMV custom Home Assistant services."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv.const import DOMAIN
from custom_components.omv.exceptions import OMVApiError
from custom_components.omv.services import (
    SERVICE_APPLY_CONFIG,
    SERVICE_COMPOSE_COMMAND,
    SERVICE_CONTAINER_COMMAND,
    SERVICE_RUN_RSYNC_JOB,
    async_setup_services,
)


@pytest.fixture
async def services_ready(coordinator, config_entry):
    """Register the services with one loaded entry and mocked command methods.

    Resets the entry state on teardown so the hass fixture does not try to
    unload a never-actually-loaded integration.
    """
    config_entry.mock_state(coordinator.hass, ConfigEntryState.LOADED)
    coordinator.async_execute_container_command = AsyncMock()
    coordinator.async_execute_compose_command = AsyncMock()
    coordinator.async_execute_rsync_job = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.api.async_apply_config = AsyncMock()
    async_setup_services(coordinator.hass)
    yield coordinator
    config_entry.mock_state(coordinator.hass, ConfigEntryState.NOT_LOADED)


async def test_services_registered_and_idempotent(coordinator) -> None:
    """Test all four services register once and re-registration is a no-op."""
    hass = coordinator.hass
    async_setup_services(hass)
    async_setup_services(hass)

    for service in (
        SERVICE_CONTAINER_COMMAND,
        SERVICE_COMPOSE_COMMAND,
        SERVICE_APPLY_CONFIG,
        SERVICE_RUN_RSYNC_JOB,
    ):
        assert hass.services.has_service(DOMAIN, service)


async def test_container_command_resolves_name(services_ready) -> None:
    """Test a container name resolves to the Docker container id."""
    coordinator = services_ready

    await coordinator.hass.services.async_call(
        DOMAIN,
        SERVICE_CONTAINER_COMMAND,
        {"container": "nginx", "command": "restart"},
        blocking=True,
    )

    coordinator.async_execute_container_command.assert_awaited_once_with("ctr-nginx", "restart")
    coordinator.async_request_refresh.assert_awaited_once()


async def test_container_command_passes_unknown_value_through(services_ready) -> None:
    """Test an unmatched container reference is passed through verbatim."""
    coordinator = services_ready

    await coordinator.hass.services.async_call(
        DOMAIN,
        SERVICE_CONTAINER_COMMAND,
        {"container": "raw-id-123", "command": "stop"},
        blocking=True,
    )

    coordinator.async_execute_container_command.assert_awaited_once_with("raw-id-123", "stop")


async def test_container_command_api_error_translated(services_ready) -> None:
    """Test an API failure surfaces as a translated HomeAssistantError."""
    coordinator = services_ready
    coordinator.async_execute_container_command.side_effect = OMVApiError("boom")

    with pytest.raises(HomeAssistantError) as excinfo:
        await coordinator.hass.services.async_call(
            DOMAIN,
            SERVICE_CONTAINER_COMMAND,
            {"container": "nginx", "command": "start"},
            blocking=True,
        )

    assert excinfo.value.translation_key == "container_command_failed"
    assert excinfo.value.translation_placeholders == {
        "command": "start",
        "resource": "nginx",
    }


async def test_container_command_rejects_invalid_command(services_ready) -> None:
    """Test the vol schema rejects commands outside the whitelist."""
    coordinator = services_ready

    with pytest.raises(vol.Invalid):
        await coordinator.hass.services.async_call(
            DOMAIN,
            SERVICE_CONTAINER_COMMAND,
            {"container": "nginx", "command": "rm -rf"},
            blocking=True,
        )

    coordinator.async_execute_container_command.assert_not_awaited()


async def test_compose_command_resolves_project_name(services_ready) -> None:
    """Test a compose project name resolves to its uuid."""
    coordinator = services_ready

    await coordinator.hass.services.async_call(
        DOMAIN,
        SERVICE_COMPOSE_COMMAND,
        {"project": "paperless", "command": "pull"},
        blocking=True,
    )

    coordinator.async_execute_compose_command.assert_awaited_once_with({"uuid": "proj-paperless", "command": "pull"})


async def test_compose_command_unknown_project(services_ready) -> None:
    """Test an unknown project raises a ServiceValidationError."""
    coordinator = services_ready

    with pytest.raises(ServiceValidationError) as excinfo:
        await coordinator.hass.services.async_call(
            DOMAIN,
            SERVICE_COMPOSE_COMMAND,
            {"project": "ghost", "command": "down"},
            blocking=True,
        )

    assert excinfo.value.translation_key == "service_project_not_found"
    coordinator.async_execute_compose_command.assert_not_awaited()


async def test_apply_config_calls_api_and_refreshes(services_ready) -> None:
    """Test apply_config refreshes around the RPC like the button."""
    coordinator = services_ready

    await coordinator.hass.services.async_call(DOMAIN, SERVICE_APPLY_CONFIG, {}, blocking=True)

    coordinator.api.async_apply_config.assert_awaited_once()
    assert coordinator.async_request_refresh.await_count == 2


async def test_run_rsync_job_resolves_name(services_ready) -> None:
    """Test an rsync job name resolves to its uuid."""
    coordinator = services_ready

    await coordinator.hass.services.async_call(
        DOMAIN,
        SERVICE_RUN_RSYNC_JOB,
        {"job": "Backup media"},
        blocking=True,
    )

    coordinator.async_execute_rsync_job.assert_awaited_once_with("rsync-uuid-0001")


async def test_run_rsync_job_unknown_job(services_ready) -> None:
    """Test an unknown rsync job raises a ServiceValidationError."""
    coordinator = services_ready

    with pytest.raises(ServiceValidationError) as excinfo:
        await coordinator.hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_RSYNC_JOB,
            {"job": "does-not-exist"},
            blocking=True,
        )

    assert excinfo.value.translation_key == "service_job_not_found"
    coordinator.async_execute_rsync_job.assert_not_awaited()


async def test_explicit_config_entry_id_targets_entry(services_ready) -> None:
    """Test an explicit config_entry_id resolves the matching entry."""
    coordinator = services_ready

    await coordinator.hass.services.async_call(
        DOMAIN,
        SERVICE_RUN_RSYNC_JOB,
        {
            "config_entry_id": coordinator.config_entry.entry_id,
            "job": "rsync-uuid-0002",
        },
        blocking=True,
    )

    coordinator.async_execute_rsync_job.assert_awaited_once_with("rsync-uuid-0002")


async def test_unknown_config_entry_id_rejected(services_ready) -> None:
    """Test an unknown config_entry_id raises a ServiceValidationError."""
    coordinator = services_ready

    with pytest.raises(ServiceValidationError) as excinfo:
        await coordinator.hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_RSYNC_JOB,
            {"config_entry_id": "bogus-entry-id", "job": "Backup media"},
            blocking=True,
        )

    assert excinfo.value.translation_key == "service_entry_not_found"


async def test_not_loaded_entry_rejected(coordinator, config_entry) -> None:
    """Test the sole entry must be loaded for auto-resolution."""
    async_setup_services(coordinator.hass)

    with pytest.raises(ServiceValidationError) as excinfo:
        await coordinator.hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_RSYNC_JOB,
            {"job": "Backup media"},
            blocking=True,
        )

    assert excinfo.value.translation_key == "service_entry_not_found"


async def test_ambiguous_entries_require_explicit_id(services_ready) -> None:
    """Test two loaded entries make config_entry_id mandatory."""
    coordinator = services_ready
    second_entry = MockConfigEntry(domain=DOMAIN, title="OMV (nas2)", data={})
    second_entry.add_to_hass(coordinator.hass)
    second_entry.mock_state(coordinator.hass, ConfigEntryState.LOADED)

    try:
        with pytest.raises(ServiceValidationError) as excinfo:
            await coordinator.hass.services.async_call(
                DOMAIN,
                SERVICE_RUN_RSYNC_JOB,
                {"job": "Backup media"},
                blocking=True,
            )
    finally:
        second_entry.mock_state(coordinator.hass, ConfigEntryState.NOT_LOADED)

    assert excinfo.value.translation_key == "service_entry_ambiguous"

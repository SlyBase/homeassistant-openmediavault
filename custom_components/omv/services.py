"""Custom Home Assistant services for the OpenMediaVault integration."""

from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import OMVDataUpdateCoordinator
from .exceptions import OMVApiError, OMVConnectionError

ATTR_COMMAND = "command"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_CONTAINER = "container"
ATTR_JOB = "job"
ATTR_PROJECT = "project"

SERVICE_APPLY_CONFIG = "apply_config"
SERVICE_COMPOSE_COMMAND = "compose_command"
SERVICE_CONTAINER_COMMAND = "container_command"
SERVICE_RUN_RSYNC_JOB = "run_rsync_job"

CONTAINER_COMMANDS = ["start", "stop", "restart", "pause", "unpause"]
COMPOSE_COMMANDS = ["up -d", "down", "start", "stop", "pull"]

# Docker container name/id charset (also matches short and full hashes).
_CONTAINER_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

APPLY_CONFIG_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})

COMPOSE_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_PROJECT): cv.string,
        vol.Required(ATTR_COMMAND): vol.In(COMPOSE_COMMANDS),
    }
)

CONTAINER_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_CONTAINER): cv.string,
        vol.Required(ATTR_COMMAND): vol.In(CONTAINER_COMMANDS),
    }
)

RUN_RSYNC_JOB_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_JOB): cv.string,
    }
)


def _async_get_coordinator(hass: HomeAssistant, call: ServiceCall) -> OMVDataUpdateCoordinator:
    """Resolve the target coordinator for a service call.

    Args:
        hass: The Home Assistant instance.
        call: The service call, optionally carrying ``config_entry_id``.

    Returns:
        The coordinator of the targeted (or sole loaded) OMV config entry.

    Raises:
        ServiceValidationError: When the entry id is unknown or not loaded,
            or when no/multiple loaded entries exist and none was specified.
    """
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id is not None:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="service_entry_not_found",
            )
        coordinator: OMVDataUpdateCoordinator = entry.runtime_data
        return coordinator

    loaded = [entry for entry in hass.config_entries.async_entries(DOMAIN) if entry.state is ConfigEntryState.LOADED]
    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_entry_not_found",
        )
    if len(loaded) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_entry_ambiguous",
        )
    sole_coordinator: OMVDataUpdateCoordinator = loaded[0].runtime_data
    return sole_coordinator


def _resolve_container_id(coordinator: OMVDataUpdateCoordinator, value: str) -> str:
    """Resolve a user-supplied container reference to a Docker container id.

    Matches against ``container_key``, ``name`` and ``container_id`` of the
    normalized container records. Unknown values are passed through verbatim
    so users can target containers OMV has not listed yet, but are validated
    against the Docker name/id charset first since OMV's compose plugin
    builds a shell command server-side from this value.

    Args:
        coordinator: The coordinator holding ``data["compose"]``.
        value: Container key, name or id from the service call.

    Returns:
        The Docker container id, or ``value`` unchanged when unmatched.

    Raises:
        ServiceValidationError: When an unmatched ``value`` contains
            characters outside the Docker name/id charset.
    """
    for record in coordinator.data.get("compose", []):
        if value in (
            record.get("container_key"),
            record.get("name"),
            record.get("container_id"),
        ):
            return str(record.get("container_id") or value)
    if not _CONTAINER_REF_RE.fullmatch(value):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_container_reference",
            translation_placeholders={"container": value},
        )
    return value


def _resolve_project_uuid(coordinator: OMVDataUpdateCoordinator, value: str) -> str:
    """Resolve a user-supplied compose project reference to its uuid.

    Args:
        coordinator: The coordinator holding ``data["compose_projects"]``.
        value: Project key, name or uuid from the service call.

    Returns:
        The compose project uuid.

    Raises:
        ServiceValidationError: When no project matches ``value``.
    """
    for record in coordinator.data.get("compose_projects", []):
        if value in (
            record.get("project_key"),
            record.get("name"),
            record.get("uuid"),
        ):
            uuid = str(record.get("uuid") or "")
            if uuid:
                return uuid
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="service_project_not_found",
        translation_placeholders={"project": value},
    )


def _resolve_rsync_uuid(coordinator: OMVDataUpdateCoordinator, value: str) -> str:
    """Resolve a user-supplied rsync job reference to its uuid.

    Args:
        coordinator: The coordinator holding ``data["rsync"]``.
        value: Rsync job key (uuid) or display name from the service call.

    Returns:
        The rsync job uuid.

    Raises:
        ServiceValidationError: When no job matches ``value``.
    """
    for record in coordinator.data.get("rsync", []):
        if value in (
            record.get("rsync_key"),
            record.get("name"),
            record.get("uuid"),
        ):
            uuid = str(record.get("uuid") or "")
            if uuid:
                return uuid
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="service_job_not_found",
        translation_placeholders={"job": value},
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the OMV domain services (idempotent).

    Registered once per Home Assistant instance and intentionally never
    deregistered on entry unload — the services are domain-global and may
    serve multiple config entries.

    Args:
        hass: The Home Assistant instance.
    """
    if hass.services.has_service(DOMAIN, SERVICE_CONTAINER_COMMAND):
        return

    async def _async_handle_container_command(call: ServiceCall) -> None:
        """Run a Docker container command on the targeted OMV host.

        Raises:
            HomeAssistantError: When the compose RPC call fails.
        """
        coordinator = _async_get_coordinator(hass, call)
        container: str = call.data[ATTR_CONTAINER]
        command: str = call.data[ATTR_COMMAND]
        container_id = _resolve_container_id(coordinator, container)
        try:
            await coordinator.async_execute_container_command(container_id, command)
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_command_failed",
                translation_placeholders={
                    "command": command,
                    "resource": container,
                },
            ) from err
        await coordinator.async_request_refresh()

    async def _async_handle_compose_command(call: ServiceCall) -> None:
        """Run a compose project command on the targeted OMV host.

        Raises:
            HomeAssistantError: When the compose RPC call fails.
        """
        coordinator = _async_get_coordinator(hass, call)
        project: str = call.data[ATTR_PROJECT]
        command: str = call.data[ATTR_COMMAND]
        uuid = _resolve_project_uuid(coordinator, project)
        try:
            await coordinator.async_execute_compose_command({"uuid": uuid, "command": command})
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="compose_command_failed",
                translation_placeholders={
                    "command": command,
                    "resource": project,
                },
            ) from err
        await coordinator.async_request_refresh()

    async def _async_handle_apply_config(call: ServiceCall) -> None:
        """Apply pending OMV configuration changes on the targeted host.

        Raises:
            HomeAssistantError: When the Config.applyChanges RPC call fails.
        """
        coordinator = _async_get_coordinator(hass, call)
        await coordinator.async_request_refresh()
        try:
            await coordinator.api.async_apply_config()
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="apply_config_failed",
            ) from err
        await coordinator.async_request_refresh()
        if hass.services.has_service("persistent_notification", "dismiss"):
            await hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": "omv_config_dirty"},
            )

    async def _async_handle_run_rsync_job(call: ServiceCall) -> None:
        """Start an rsync job on the targeted OMV host (fire-and-forget).

        Raises:
            HomeAssistantError: When the Rsync.execute RPC call fails.
        """
        coordinator = _async_get_coordinator(hass, call)
        job: str = call.data[ATTR_JOB]
        uuid = _resolve_rsync_uuid(coordinator, job)
        try:
            await coordinator.async_execute_rsync_job(uuid)
        except (OMVApiError, OMVConnectionError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rsync_execute_failed",
                translation_placeholders={"name": job},
            ) from err
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CONTAINER_COMMAND,
        _async_handle_container_command,
        schema=CONTAINER_COMMAND_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPOSE_COMMAND,
        _async_handle_compose_command,
        schema=COMPOSE_COMMAND_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_CONFIG,
        _async_handle_apply_config,
        schema=APPLY_CONFIG_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_RSYNC_JOB,
        _async_handle_run_rsync_job,
        schema=RUN_RSYNC_JOB_SCHEMA,
    )


__all__ = ["async_setup_services"]

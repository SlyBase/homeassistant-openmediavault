"""Config flow for the OpenMediaVault integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_ZFS_POOLS,
    CONF_SMART_DISABLED,
    CONF_VIRTUAL_PASSTHROUGH,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import OMVDataUpdateCoordinator
from .exceptions import OMVAuthError, OMVConnectionError
from .omv_api import OMVAPI

_LOGGER = logging.getLogger(__name__)

_DEFAULT_USER_FORM_VALUES: dict[str, Any] = {
    CONF_HOST: "",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "",
    CONF_PORT: DEFAULT_PORT,
    CONF_SSL: DEFAULT_SSL,
    CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
}

_RESOURCE_FIELDS: tuple[str, ...] = (
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_ZFS_POOLS,
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
)


class OMVConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OMV."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow and persist the latest form values."""
        self._user_form_values = dict(_DEFAULT_USER_FORM_VALUES)

    def _update_user_form_values(self, user_input: dict[str, Any]) -> None:
        """Persist entered values while always clearing the password field."""
        self._user_form_values.update(
            {
                CONF_HOST: user_input.get(CONF_HOST, self._user_form_values[CONF_HOST]),
                CONF_USERNAME: user_input.get(
                    CONF_USERNAME, self._user_form_values[CONF_USERNAME]
                ),
                CONF_PORT: user_input.get(CONF_PORT, self._user_form_values[CONF_PORT]),
                CONF_SSL: user_input.get(CONF_SSL, self._user_form_values[CONF_SSL]),
                CONF_VERIFY_SSL: user_input.get(
                    CONF_VERIFY_SSL, self._user_form_values[CONF_VERIFY_SSL]
                ),
                CONF_PASSWORD: "",
            }
        )

    def _build_user_schema(self) -> vol.Schema:
        """Build the user schema from the latest remembered values."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=self._user_form_values[CONF_HOST]
                ): str,
                vol.Required(
                    CONF_USERNAME, default=self._user_form_values[CONF_USERNAME]
                ): str,
                vol.Required(
                    CONF_PASSWORD, default=self._user_form_values[CONF_PASSWORD]
                ): str,
                vol.Optional(
                    CONF_PORT, default=self._user_form_values[CONF_PORT]
                ): int,
                vol.Optional(
                    CONF_SSL, default=self._user_form_values[CONF_SSL]
                ): bool,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=self._user_form_values[CONF_VERIFY_SSL],
                ): bool,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._update_user_form_values(user_input)
            _LOGGER.debug(
                "OMV config flow submit host=%r username=%r port=%s ssl=%s "
                "verify_ssl=%s password_length=%d "
                "password_has_outer_whitespace=%s",
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input.get(CONF_PORT, DEFAULT_PORT),
                user_input.get(CONF_SSL, DEFAULT_SSL),
                user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                len(user_input[CONF_PASSWORD]),
                user_input[CONF_PASSWORD] != user_input[CONF_PASSWORD].strip(),
            )
            api = OMVAPI(
                host=user_input[CONF_HOST],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                port=user_input.get(CONF_PORT, DEFAULT_PORT),
                ssl=user_input.get(CONF_SSL, DEFAULT_SSL),
                verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                source="config_flow",
            )
            try:
                system_info = await api.async_connect()
            except OMVAuthError as err:
                _LOGGER.debug(
                    "OMV config flow invalid_auth for host=%r: %s",
                    user_input[CONF_HOST],
                    err,
                )
                errors["base"] = "invalid_auth"
            except OMVConnectionError as err:
                _LOGGER.debug(
                    "OMV config flow cannot_connect for host=%r: %s",
                    user_input[CONF_HOST],
                    err,
                )
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during OMV setup")
                errors["base"] = "unknown"
            else:
                hostname = str(system_info.get("hostname") or user_input[CONF_HOST])
                await self.async_set_unique_id(hostname)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"OMV ({hostname})",
                    data=user_input,
                )
            finally:
                await api.async_close()

        _LOGGER.debug(
            "OMV config flow show form host=%r username=%r port=%s ssl=%s "
            "verify_ssl=%s errors=%s",
            self._user_form_values[CONF_HOST],
            self._user_form_values[CONF_USERNAME],
            self._user_form_values[CONF_PORT],
            self._user_form_values[CONF_SSL],
            self._user_form_values[CONF_VERIFY_SSL],
            errors,
        )

        return self.async_show_form(
            step_id="user",
            data_schema=self._build_user_schema(),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OMVOptionsFlow:
        """Return the options flow handler."""
        return OMVOptionsFlow(config_entry)


class OMVOptionsFlow(OptionsFlow):
    """Handle OMV options."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options flow."""
        if user_input is not None:
            data = dict(user_input)
            if data.get(CONF_VIRTUAL_PASSTHROUGH):
                data[CONF_SMART_DISABLED] = True
            for field in _RESOURCE_FIELDS:
                if field not in data and field in self._entry.options:
                    data[field] = list(self._entry.options.get(field, []))
            return self.async_create_entry(data=data)

        inventory = self._get_inventory()
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self._entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_SMART_DISABLED,
                    default=self._entry.options.get(CONF_SMART_DISABLED, False)
                    or self._entry.options.get(CONF_VIRTUAL_PASSTHROUGH, False),
                ): bool,
                vol.Optional(
                    CONF_VIRTUAL_PASSTHROUGH,
                    default=self._entry.options.get(CONF_VIRTUAL_PASSTHROUGH, False),
                ): bool,
                vol.Optional(
                    CONF_SELECTED_DISKS,
                    default=self._default_selection(
                        CONF_SELECTED_DISKS,
                        inventory[CONF_SELECTED_DISKS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_DISKS]),
                vol.Optional(
                    CONF_SELECTED_FILESYSTEMS,
                    default=self._default_selection(
                        CONF_SELECTED_FILESYSTEMS,
                        inventory[CONF_SELECTED_FILESYSTEMS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_FILESYSTEMS]),
                vol.Optional(
                    CONF_SELECTED_SERVICES,
                    default=self._default_selection(
                        CONF_SELECTED_SERVICES,
                        inventory[CONF_SELECTED_SERVICES],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_SERVICES]),
                vol.Optional(
                    CONF_SELECTED_NETWORK_INTERFACES,
                    default=self._default_selection(
                        CONF_SELECTED_NETWORK_INTERFACES,
                        inventory[CONF_SELECTED_NETWORK_INTERFACES],
                    ),
                ): self._build_multi_select(
                    inventory[CONF_SELECTED_NETWORK_INTERFACES]
                ),
                vol.Optional(
                    CONF_SELECTED_RAIDS,
                    default=self._default_selection(
                        CONF_SELECTED_RAIDS,
                        inventory[CONF_SELECTED_RAIDS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_RAIDS]),
                vol.Optional(
                    CONF_SELECTED_ZFS_POOLS,
                    default=self._default_selection(
                        CONF_SELECTED_ZFS_POOLS,
                        inventory[CONF_SELECTED_ZFS_POOLS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_ZFS_POOLS]),
                vol.Optional(
                    CONF_SELECTED_COMPOSE_PROJECTS,
                    default=self._default_selection(
                        CONF_SELECTED_COMPOSE_PROJECTS,
                        inventory[CONF_SELECTED_COMPOSE_PROJECTS],
                    ),
                ): self._build_multi_select(
                    inventory[CONF_SELECTED_COMPOSE_PROJECTS]
                ),
                vol.Optional(
                    CONF_SELECTED_CONTAINERS,
                    default=self._default_selection(
                        CONF_SELECTED_CONTAINERS,
                        inventory[CONF_SELECTED_CONTAINERS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_CONTAINERS]),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )

    def _get_inventory(self) -> dict[str, list[dict[str, str]]]:
        """Load live inventory and merge it with persisted values."""
        live_inventory: dict[str, list[dict[str, str]]] = {
            field: [] for field in _RESOURCE_FIELDS
        }

        coordinator = getattr(self._entry, "runtime_data", None)
        if coordinator is not None:
            try:
                live_inventory = coordinator.get_live_inventory()
            except Exception:
                _LOGGER.debug(
                    "Falling back to cached runtime data for options inventory",
                    exc_info=True,
                )
                cached_data = getattr(coordinator, "data", None)
                if isinstance(cached_data, dict):
                    live_inventory = OMVDataUpdateCoordinator.build_inventory(cached_data)

        merged_inventory: dict[str, list[dict[str, str]]] = {}
        for field in _RESOURCE_FIELDS:
            persisted_values = self._entry.options.get(field, [])
            persisted_options = [
                {"value": str(value), "label": str(value)} for value in persisted_values
            ]
            merged_inventory[field] = self._merge_inventory_options(
                live_inventory.get(field, []),
                persisted_options,
            )

        return merged_inventory

    def _default_selection(
        self,
        field: str,
        options: Sequence[Mapping[str, str]],
    ) -> list[str]:
        """Return the default selection for a resource category."""
        if field in self._entry.options:
            return list(self._entry.options.get(field, []))
        if any(resource_field in self._entry.options for resource_field in _RESOURCE_FIELDS):
            return []
        return [str(option["value"]) for option in options]

    def _build_multi_select(
        self,
        options: Sequence[Mapping[str, str]],
    ) -> selector.SelectSelector:
        """Build a multi-select selector for options flows."""
        merged = {str(option["value"]): str(option["label"]) for option in options}
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": value, "label": label}
                    for value, label in merged.items()
                ],
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    def _merge_inventory_options(
        self,
        live_options: Sequence[Mapping[str, str]],
        persisted_options: Sequence[Mapping[str, str]],
    ) -> list[dict[str, str]]:
        """Merge live and persisted options without dropping missing persisted values."""
        merged: dict[str, str] = {}
        for option in list(live_options) + list(persisted_options):
            value = str(option["value"])
            merged.setdefault(value, str(option["label"]))
        return [
            {"value": value, "label": merged[value]}
            for value in sorted(merged, key=str.casefold)
        ]

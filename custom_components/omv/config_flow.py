"""Config flow for the OpenMediaVault integration."""

from __future__ import annotations

import logging
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

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SMART_DISABLED,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
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
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options flow."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self._entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=10, max=3600)),
                    vol.Optional(
                        CONF_SMART_DISABLED,
                        default=self._entry.options.get(CONF_SMART_DISABLED, False),
                    ): bool,
                }
            ),
        )

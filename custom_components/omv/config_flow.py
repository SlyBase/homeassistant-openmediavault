"""Config flow for the OpenMediaVault integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import session_handoff, totp
from .const import (
    CONF_MAX_CONSECUTIVE_FAILURES,
    CONF_REBOOT_REPAIR_DISABLED,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
    CONF_SELECTED_CRON_JOBS,
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_VMS,
    CONF_SELECTED_ZFS_POOLS,
    CONF_SMART_INTERVAL,
    CONF_SMART_POLLING_DISABLED,
    CONF_TOTP_SECRET,
    CONF_UPDATE_TRACKING_DISABLED,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import OMVDataUpdateCoordinator
from .exceptions import OMVAuthError, OMVConnectionError, OMVTwoFactorRequiredError
from .omv_api import OMVAPI

_LOGGER = logging.getLogger(__name__)

CONF_TOTP_CODE = "code"

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
    CONF_SELECTED_VMS,
)

# Cron is deliberately NOT a resource field: its selection is an opt-in for
# button creation (default: none) and must never inherit the select-all
# default that _default_selection applies to _RESOURCE_FIELDS.
_OPT_IN_FIELDS: tuple[str, ...] = (CONF_SELECTED_CRON_JOBS,)


class OMVConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OMV."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow and persist the latest form values."""
        self._user_form_values = dict(_DEFAULT_USER_FORM_VALUES)
        self._pending_api: OMVAPI | None = None
        self._pending_user_input: dict[str, Any] | None = None

    def _update_user_form_values(self, user_input: dict[str, Any]) -> None:
        """Persist entered values while always clearing the password field."""
        self._user_form_values.update(
            {
                CONF_HOST: user_input.get(CONF_HOST, self._user_form_values[CONF_HOST]),
                CONF_USERNAME: user_input.get(CONF_USERNAME, self._user_form_values[CONF_USERNAME]),
                CONF_PORT: user_input.get(CONF_PORT, self._user_form_values[CONF_PORT]),
                CONF_SSL: user_input.get(CONF_SSL, self._user_form_values[CONF_SSL]),
                CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, self._user_form_values[CONF_VERIFY_SSL]),
                CONF_PASSWORD: "",
            }
        )

    def _build_user_schema(self) -> vol.Schema:
        """Build the user schema from the latest remembered values."""
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._user_form_values[CONF_HOST]): str,
                vol.Required(CONF_USERNAME, default=self._user_form_values[CONF_USERNAME]): str,
                vol.Required(CONF_PASSWORD, default=self._user_form_values[CONF_PASSWORD]): str,
                vol.Optional(CONF_PORT, default=self._user_form_values[CONF_PORT]): int,
                vol.Optional(CONF_SSL, default=self._user_form_values[CONF_SSL]): bool,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=self._user_form_values[CONF_VERIFY_SSL],
                ): bool,
            }
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguration of an existing OMV entry (e.g. switching to HTTPS or 2FA)."""
        entry = self._get_reconfigure_entry()
        if user_input is None:
            self._user_form_values.update(
                {
                    CONF_HOST: entry.data.get(CONF_HOST, ""),
                    CONF_USERNAME: entry.data.get(CONF_USERNAME, "admin"),
                    CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
                    CONF_SSL: entry.data.get(CONF_SSL, DEFAULT_SSL),
                    CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                }
            )
        return await self.async_step_user(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle reauthentication triggered by HA (e.g. a changed password or newly required 2FA)."""
        entry = self._get_reauth_entry()
        self._user_form_values.update(
            {
                CONF_HOST: entry.data.get(CONF_HOST, ""),
                CONF_USERNAME: entry.data.get(CONF_USERNAME, "admin"),
                CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
                CONF_SSL: entry.data.get(CONF_SSL, DEFAULT_SSL),
                CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            }
        )
        return await self.async_step_user()

    async def _finish_flow(
        self,
        hostname: str,
        data: dict[str, Any],
        api: OMVAPI,
        system_info: dict[str, Any],
    ) -> ConfigFlowResult:
        """Create/update the entry after a successful login.

        Hands off the already-authenticated ``api``/``system_info`` to the
        setup that Home Assistant triggers immediately after this flow
        finishes, so it doesn't have to open a brand new (and, for 2FA
        accounts, immediately challenged) OMV login. If the flow aborts
        instead (e.g. a unique-ID mismatch), the hand-off never happens and
        the session is closed here.

        For reauth/reconfigure the hand-off is keyed by the entry's existing
        ``unique_id`` — that is the key ``async_setup_entry`` pops — instead
        of the freshly computed hostname, so the two can never diverge
        (Issue #55). For brand new entries both are identical because
        ``async_set_unique_id(hostname)`` just ran.
        """
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            entry = self._get_reconfigure_entry() if self.source == SOURCE_RECONFIGURE else self._get_reauth_entry()
            try:
                self._abort_if_unique_id_mismatch()
            except Exception:
                await api.async_close()
                raise
            session_handoff.store(entry.unique_id or hostname, api, system_info)
            return self.async_update_reload_and_abort(
                entry,
                title=f"OMV ({hostname})",
                data=data,
            )
        try:
            self._abort_if_unique_id_configured()
        except Exception:
            await api.async_close()
            raise
        session_handoff.store(hostname, api, system_info)
        return self.async_create_entry(title=f"OMV ({hostname})", data=data)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._update_user_form_values(user_input)
            _LOGGER.debug(
                "OMV config flow submit host=%r username=%r port=%s ssl=%s "
                "verify_ssl=%s password_has_outer_whitespace=%s",
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input.get(CONF_PORT, DEFAULT_PORT),
                user_input.get(CONF_SSL, DEFAULT_SSL),
                user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
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
            except OMVTwoFactorRequiredError as err:
                _LOGGER.debug(
                    "OMV config flow requires two-factor authentication for host=%r: %s",
                    user_input[CONF_HOST],
                    err,
                )
                # Keep the api instance (and its session cookie) alive — OMV
                # binds the pending login to it and the totp step reuses it.
                self._pending_api = api
                self._pending_user_input = dict(user_input)
                return await self.async_step_totp()
            except OMVAuthError as err:
                _LOGGER.debug(
                    "OMV config flow invalid_auth for host=%r: %s",
                    user_input[CONF_HOST],
                    err,
                )
                errors["base"] = "invalid_auth"
                await api.async_close()
            except OMVConnectionError as err:
                _LOGGER.debug(
                    "OMV config flow cannot_connect for host=%r: %s",
                    user_input[CONF_HOST],
                    err,
                )
                errors["base"] = "cannot_connect"
                await api.async_close()
            except Exception:
                _LOGGER.exception("Unexpected error during OMV setup")
                errors["base"] = "unknown"
                await api.async_close()
            else:
                hostname = str(system_info.get("hostname") or user_input[CONF_HOST])
                await self.async_set_unique_id(hostname)
                return await self._finish_flow(hostname, user_input, api, system_info)

        _LOGGER.debug(
            "OMV config flow show form host=%r username=%r port=%s ssl=%s verify_ssl=%s errors=%s",
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

    def _existing_totp_secret(self) -> str | None:
        """Return the TOTP secret already stored on the entry being reauthed/reconfigured."""
        if self.source == SOURCE_RECONFIGURE:
            entry: ConfigEntry | None = self._get_reconfigure_entry()
        elif self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
        else:
            entry = None
        if entry is None:
            return None
        secret = entry.data.get(CONF_TOTP_SECRET)
        return secret if isinstance(secret, str) and secret else None

    async def async_step_totp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the second step of a two-factor OMV login.

        Accepts either a one-time verification code or the Base32 TOTP secret
        itself (exactly one of the two). When the secret is provided, the code
        is generated locally, verified against OMV, and the secret is stored
        in the entry data so the integration can answer future 2FA challenges
        (session expiry, HA restarts) without user interaction (Issue #55).
        """
        errors: dict[str, str] = {}
        api = self._pending_api

        if user_input is not None and api is not None:
            code = str(user_input.get(CONF_TOTP_CODE) or "").strip()
            secret = str(user_input.get(CONF_TOTP_SECRET) or "").strip()
            if bool(code) == bool(secret):
                errors["base"] = "totp_code_or_secret_required"
            elif secret and not totp.is_valid_secret(secret):
                errors["base"] = "invalid_totp_secret"
            else:
                try:
                    system_info = await api.async_submit_two_factor_code(code or totp.generate_code(secret))
                except OMVAuthError as err:
                    _LOGGER.debug("OMV config flow 2FA verification rejected: %s", err)
                    errors["base"] = "invalid_totp_secret" if secret else "invalid_totp_code"
                except OMVConnectionError as err:
                    _LOGGER.debug("OMV config flow cannot_connect during 2FA verification: %s", err)
                    errors["base"] = "cannot_connect"
                    await api.async_close()
                    self._pending_api = None
                except Exception:
                    _LOGGER.exception("Unexpected error during OMV 2FA verification")
                    errors["base"] = "unknown"
                    await api.async_close()
                    self._pending_api = None
                else:
                    assert self._pending_user_input is not None
                    data = dict(self._pending_user_input)
                    # Keep a previously stored secret when the user verified with
                    # a one-time code during reauth/reconfigure — dropping it
                    # would silently disable automatic re-logins again.
                    stored_secret = secret or self._existing_totp_secret()
                    if stored_secret:
                        data[CONF_TOTP_SECRET] = stored_secret
                        api.set_totp_secret(stored_secret)
                    hostname = str(system_info.get("hostname") or data[CONF_HOST])
                    self._pending_api = None
                    await self.async_set_unique_id(hostname)
                    return await self._finish_flow(hostname, data, api, system_info)

        return self.async_show_form(
            step_id="totp",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TOTP_CODE): str,
                    vol.Optional(CONF_TOTP_SECRET): str,
                }
            ),
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

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options flow."""
        if user_input is not None:
            data = dict(user_input)
            for field in (*_RESOURCE_FIELDS, *_OPT_IN_FIELDS):
                if field not in data and field in self._entry.options:
                    data[field] = list(self._entry.options.get(field, []))
            return self.async_create_entry(data=data)

        inventory = self._get_inventory()
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self._entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_REBOOT_REPAIR_DISABLED,
                    default=self._entry.options.get(CONF_REBOOT_REPAIR_DISABLED, False),
                ): bool,
                vol.Optional(
                    CONF_UPDATE_TRACKING_DISABLED,
                    default=self._entry.options.get(CONF_UPDATE_TRACKING_DISABLED, False),
                ): bool,
                vol.Optional(
                    CONF_SMART_INTERVAL,
                    default=self._entry.options.get(
                        CONF_SMART_INTERVAL,
                        self._entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ),
                ): vol.All(int, vol.Range(min=10, max=86400)),
                vol.Optional(
                    CONF_SMART_POLLING_DISABLED,
                    default=self._entry.options.get(CONF_SMART_POLLING_DISABLED, False),
                ): bool,
                vol.Optional(
                    CONF_MAX_CONSECUTIVE_FAILURES,
                    default=self._entry.options.get(
                        CONF_MAX_CONSECUTIVE_FAILURES,
                        DEFAULT_MAX_CONSECUTIVE_FAILURES,
                    ),
                ): vol.All(int, vol.Range(min=1, max=1000)),
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
                ): self._build_multi_select(inventory[CONF_SELECTED_NETWORK_INTERFACES]),
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
                ): self._build_multi_select(inventory[CONF_SELECTED_COMPOSE_PROJECTS]),
                vol.Optional(
                    CONF_SELECTED_CONTAINERS,
                    default=self._default_selection(
                        CONF_SELECTED_CONTAINERS,
                        inventory[CONF_SELECTED_CONTAINERS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_CONTAINERS]),
                vol.Optional(
                    CONF_SELECTED_VMS,
                    default=self._default_selection(
                        CONF_SELECTED_VMS,
                        inventory[CONF_SELECTED_VMS],
                    ),
                ): self._build_multi_select(inventory[CONF_SELECTED_VMS]),
                vol.Optional(
                    CONF_SELECTED_CRON_JOBS,
                    default=list(self._entry.options.get(CONF_SELECTED_CRON_JOBS, [])),
                ): self._build_multi_select(inventory[CONF_SELECTED_CRON_JOBS]),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )

    def _get_inventory(self) -> dict[str, list[dict[str, str]]]:
        """Load live inventory and merge it with persisted values."""
        live_inventory: dict[str, list[dict[str, str]]] = {field: [] for field in (*_RESOURCE_FIELDS, *_OPT_IN_FIELDS)}

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
        for field in (*_RESOURCE_FIELDS, *_OPT_IN_FIELDS):
            persisted_values = self._entry.options.get(field, [])
            persisted_options = [{"value": str(value), "label": str(value)} for value in persisted_values]
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
                options=[selector.SelectOptionDict(value=value, label=label) for value, label in merged.items()],
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
        return [{"value": value, "label": merged[value]} for value in sorted(merged, key=str.casefold)]

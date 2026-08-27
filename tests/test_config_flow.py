"""Tests for the OMV config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omv.const import (
    CONF_MAX_CONSECUTIVE_FAILURES,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
    CONF_SELECTED_CRON_JOBS,
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_ZFS_POOLS,
    CONF_SMART_INTERVAL,
    CONF_SMART_POLLING_DISABLED,
    CONF_TOTP_SECRET,
    CONF_UPDATE_TRACKING_DISABLED,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DOMAIN,
)
from custom_components.omv.exceptions import OMVAuthError, OMVTwoFactorRequiredError

USER_INPUT = {
    CONF_HOST: "192.0.2.10",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_PORT: 80,
    CONF_SSL: False,
    CONF_VERIFY_SSL: True,
}


def _field_marker(schema: vol.Schema, field_name: str) -> vol.Marker:
    """Return the schema marker for a field."""
    for marker in schema.schema:
        if marker.schema == field_name:
            return marker
    raise AssertionError(f"Field {field_name} not found in schema")


def _selector_values(field_selector: selector.SelectSelector) -> list[str]:
    """Extract selector option values for assertions."""
    config = field_selector.config
    options = config["options"] if isinstance(config, dict) else config.options
    return [str(option["value"]) for option in options]


@pytest.mark.asyncio
async def test_flow_user_success(hass) -> None:
    """Test the happy path for the user flow."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        assert result["type"] == "form"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] == "create_entry"
    assert result["title"] == "OMV (nas)"


@pytest.mark.asyncio
async def test_flow_auth_error(hass) -> None:
    """Test the invalid_auth path."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVAuthError("invalid")),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"

    defaults = result["data_schema"]({})

    assert defaults[CONF_HOST] == USER_INPUT[CONF_HOST]
    assert defaults[CONF_USERNAME] == USER_INPUT[CONF_USERNAME]
    assert defaults[CONF_PORT] == USER_INPUT[CONF_PORT]
    assert defaults[CONF_SSL] is USER_INPUT[CONF_SSL]
    assert defaults[CONF_VERIFY_SSL] is USER_INPUT[CONF_VERIFY_SSL]
    assert defaults[CONF_PASSWORD] == ""


@pytest.mark.asyncio
async def test_flow_totp_required_then_success(hass) -> None:
    """Test the 2FA path: challenge required on step 1, code accepted on step 2."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        assert result["type"] == "form"
        assert result["step_id"] == "totp"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})

    assert result["type"] == "create_entry"
    assert result["title"] == "OMV (nas)"


@pytest.mark.asyncio
async def test_flow_totp_wrong_code(hass) -> None:
    """Test that a rejected TOTP code shows the invalid_totp_code error and stays on the totp step."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(side_effect=OMVAuthError("Two-factor verification failed")),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})

    assert result["type"] == "form"
    assert result["step_id"] == "totp"
    assert result["errors"]["base"] == "invalid_totp_code"


TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.asyncio
async def test_flow_totp_secret_verifies_and_stores_secret(hass) -> None:
    """Providing the TOTP secret verifies a locally generated code and stores the secret."""
    submit_mock = AsyncMock(return_value={"hostname": "nas"})
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=submit_mock,
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        assert result["step_id"] == "totp"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_TOTP_SECRET: TOTP_SECRET},
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_TOTP_SECRET] == TOTP_SECRET
    submitted_code = submit_mock.await_args.args[0]
    assert len(submitted_code) == 6 and submitted_code.isdigit()


@pytest.mark.asyncio
async def test_flow_totp_neither_code_nor_secret_shows_error(hass) -> None:
    """Submitting the totp step without code and secret shows a validation error."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "form"
    assert result["step_id"] == "totp"
    assert result["errors"]["base"] == "totp_code_or_secret_required"


@pytest.mark.asyncio
async def test_flow_totp_both_code_and_secret_shows_error(hass) -> None:
    """Submitting both a code and a secret shows the exactly-one validation error."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"code": "123456", CONF_TOTP_SECRET: TOTP_SECRET},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "totp_code_or_secret_required"


@pytest.mark.asyncio
async def test_flow_totp_invalid_secret_shows_error(hass) -> None:
    """A secret that is not valid Base32 is rejected without contacting OMV."""
    submit_mock = AsyncMock()
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=submit_mock,
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_TOTP_SECRET: "not-base32!!"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_totp_secret"
    submit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_flow_totp_secret_rejected_by_omv_shows_error(hass) -> None:
    """A well-formed secret whose generated code OMV rejects shows invalid_totp_secret."""
    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(side_effect=OMVAuthError("Two-factor verification failed")),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_TOTP_SECRET: TOTP_SECRET},
        )

    assert result["type"] == "form"
    assert result["step_id"] == "totp"
    assert result["errors"]["base"] == "invalid_totp_secret"


@pytest.mark.asyncio
async def test_flow_reauth_with_totp_secret_updates_entry(hass) -> None:
    """Reauth with a TOTP secret stores it on the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        assert result["step_id"] == "totp"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_TOTP_SECRET: TOTP_SECRET},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOTP_SECRET] == TOTP_SECRET


@pytest.mark.asyncio
async def test_flow_reauth_with_code_keeps_stored_totp_secret(hass) -> None:
    """Reauthenticating with a one-time code must not drop a previously stored secret."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data={**USER_INPUT, CONF_TOTP_SECRET: TOTP_SECRET},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOTP_SECRET] == TOTP_SECRET


@pytest.mark.asyncio
async def test_flow_reauth_stores_handoff_under_entry_unique_id(hass) -> None:
    """The reauth hand-off is keyed by entry.unique_id, not the fresh hostname (Issue #55)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    stored_keys: list[str] = []

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "othernas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch(
            "custom_components.omv.config_flow.OMVConfigFlow._abort_if_unique_id_mismatch",
            new=lambda self, **kwargs: None,
        ),
        patch(
            "custom_components.omv.config_flow.session_handoff.store",
            side_effect=lambda key, api, info: stored_keys.append(key),
        ),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    # Setup pops the hand-off under entry.unique_id — storing it under the
    # freshly computed hostname ("othernas") would strand it forever.
    assert stored_keys == ["nas"]


@pytest.mark.asyncio
async def test_flow_duplicate_abort(hass, config_entry) -> None:
    """Test duplicate hostnames are rejected via unique_id."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=config_entry.data,
        options=config_entry.options,
    )
    config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_flow_reconfigure_prefills_existing_values(hass) -> None:
    """Test the reconfigure form is pre-filled from the existing entry, password blank."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    defaults = result["data_schema"]({})
    assert defaults[CONF_HOST] == USER_INPUT[CONF_HOST]
    assert defaults[CONF_USERNAME] == USER_INPUT[CONF_USERNAME]
    assert defaults[CONF_PORT] == USER_INPUT[CONF_PORT]
    assert defaults[CONF_SSL] is USER_INPUT[CONF_SSL]
    assert defaults[CONF_VERIFY_SSL] is USER_INPUT[CONF_VERIFY_SSL]
    assert defaults[CONF_PASSWORD] == ""


@pytest.mark.asyncio
async def test_flow_reconfigure_success_switches_to_https_and_2fa(hass) -> None:
    """Test reconfiguring an entry to HTTPS + 2FA updates the entry in place (no duplicate)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    new_input = {
        **USER_INPUT,
        CONF_PORT: 443,
        CONF_SSL: True,
    }

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], new_input)
        assert result["type"] == "form"
        assert result["step_id"] == "totp"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PORT] == 443
    assert entry.data[CONF_SSL] is True
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.asyncio
async def test_flow_reconfigure_wrong_account_aborts(hass) -> None:
    """Test reconfiguring against a different OMV host is rejected via unique_id mismatch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "othernas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_HOST: "192.0.2.99"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "unique_id_mismatch"


@pytest.mark.asyncio
async def test_flow_reauth_totp_now_required_updates_entry(hass) -> None:
    """Test reauth (e.g. triggered by OMV newly requiring 2FA) updates the entry via the same totp step."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(side_effect=OMVTwoFactorRequiredError("2FA required", challenge_kind="totp")),
        ),
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_submit_two_factor_code",
            new=AsyncMock(return_value={"hostname": "nas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
        patch("custom_components.omv.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == "form"
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        assert result["type"] == "form"
        assert result["step_id"] == "totp"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.asyncio
async def test_flow_reauth_wrong_account_aborts(hass) -> None:
    """Test reauth against a different OMV host is rejected via unique_id mismatch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        unique_id="nas",
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.omv.config_flow.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "othernas"}),
        ),
        patch("custom_components.omv.config_flow.OMVAPI.async_close", new=AsyncMock()),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_HOST: "192.0.2.99"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "unique_id_mismatch"


@pytest.mark.asyncio
async def test_options_flow_uses_live_inventory_and_defaults_to_all(hass, config_entry) -> None:
    """Test the options flow exposes unfiltered live inventory with defaults."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
            CONF_SELECTED_SERVICES: ["ssh"],
            CONF_SELECTED_NETWORK_INTERFACES: ["net-1"],
            CONF_SELECTED_RAIDS: ["md0"],
            CONF_SELECTED_ZFS_POOLS: ["tank"],
            CONF_SELECTED_COMPOSE_PROJECTS: ["paperless"],
            CONF_SELECTED_CONTAINERS: ["ctr-paperless-app"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [
                    {"value": "sda", "label": "sda"},
                    {"value": "sdb", "label": "sdb"},
                ],
                CONF_SELECTED_FILESYSTEMS: [
                    {"value": "fs-1", "label": "data"},
                    {"value": "fs-2", "label": "backup"},
                ],
                CONF_SELECTED_SERVICES: [{"value": "ssh", "label": "SSH"}],
                CONF_SELECTED_NETWORK_INTERFACES: [
                    {"value": "net-1", "label": "eth0"},
                    {"value": "net-2", "label": "eth1"},
                ],
                CONF_SELECTED_RAIDS: [{"value": "md0", "label": "md0"}],
                CONF_SELECTED_ZFS_POOLS: [{"value": "tank", "label": "tank"}],
                CONF_SELECTED_COMPOSE_PROJECTS: [
                    {"value": "paperless", "label": "paperless (2)"},
                ],
                CONF_SELECTED_CONTAINERS: [
                    {"value": "ctr-paperless-app", "label": "paperless-app [paperless]"},
                    {"value": "ctr-db", "label": "db [paperless]"},
                ],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] == "form"
    defaults = result["data_schema"]({})
    assert defaults[CONF_SELECTED_DISKS] == ["sda"]
    assert defaults[CONF_SELECTED_COMPOSE_PROJECTS] == ["paperless"]
    assert defaults[CONF_SELECTED_CONTAINERS] == ["ctr-paperless-app"]

    disk_marker = _field_marker(result["data_schema"], CONF_SELECTED_DISKS)
    disk_selector = result["data_schema"].schema[disk_marker]
    assert _selector_values(disk_selector) == ["sda", "sdb"]


@pytest.mark.asyncio
async def test_options_flow_exposes_smart_polling_controls(hass, config_entry) -> None:
    """The options flow exposes the SMART interval and disable toggle (#41)."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {"get_live_inventory": lambda self=None: {}},
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    # SMART interval defaults to the scan interval; the disable toggle defaults to off.
    assert defaults[CONF_SMART_INTERVAL] == defaults[CONF_SCAN_INTERVAL]
    assert defaults[CONF_SMART_POLLING_DISABLED] is False


@pytest.mark.asyncio
async def test_options_flow_honors_stored_smart_polling_controls(hass) -> None:
    """A stored SMART interval/disable toggle is reflected as the schema default (#41)."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=USER_INPUT,
        options={CONF_SMART_INTERVAL: 3600, CONF_SMART_POLLING_DISABLED: True},
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {"get_live_inventory": lambda self=None: {}},
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_SMART_INTERVAL] == 3600
    assert defaults[CONF_SMART_POLLING_DISABLED] is True


@pytest.mark.asyncio
async def test_options_flow_exposes_update_tracking_control(hass, config_entry) -> None:
    """The options flow exposes the update-tracking disable toggle, off by default (#66)."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {"get_live_inventory": lambda self=None: {}},
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_UPDATE_TRACKING_DISABLED] is False


@pytest.mark.asyncio
async def test_options_flow_honors_stored_update_tracking_disabled(hass) -> None:
    """A stored update-tracking disable flag is reflected as the schema default (#66)."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=USER_INPUT,
        options={CONF_UPDATE_TRACKING_DISABLED: True},
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {"get_live_inventory": lambda self=None: {}},
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_UPDATE_TRACKING_DISABLED] is True


@pytest.mark.asyncio
async def test_options_flow_exposes_max_consecutive_failures_control(hass, config_entry) -> None:
    """The options flow exposes the cached-data grace window, defaulting to 3 (#82)."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {"get_live_inventory": lambda self=None: {}},
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_MAX_CONSECUTIVE_FAILURES] == DEFAULT_MAX_CONSECUTIVE_FAILURES


@pytest.mark.asyncio
async def test_options_flow_max_consecutive_failures_saves_correctly(hass, config_entry) -> None:
    """Submitting a custom grace window persists it into the config entry options (#82)."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {"get_live_inventory": lambda self=None: {}},
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MAX_CONSECUTIVE_FAILURES: 10},
    )

    assert result["data"][CONF_MAX_CONSECUTIVE_FAILURES] == 10


@pytest.mark.asyncio
async def test_options_flow_defaults_to_all_when_selection_was_never_set(hass, config_entry) -> None:
    """Test new entries default to all currently available resources."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [
                    {"value": "sda", "label": "sda"},
                    {"value": "sdb", "label": "sdb"},
                ],
                CONF_SELECTED_FILESYSTEMS: [{"value": "fs-1", "label": "data"}],
                CONF_SELECTED_SERVICES: [{"value": "ssh", "label": "SSH"}],
                CONF_SELECTED_NETWORK_INTERFACES: [{"value": "net-1", "label": "eth0"}],
                CONF_SELECTED_RAIDS: [{"value": "md0", "label": "md0"}],
                CONF_SELECTED_ZFS_POOLS: [{"value": "tank", "label": "tank"}],
                CONF_SELECTED_COMPOSE_PROJECTS: [{"value": "paperless", "label": "paperless (2)"}],
                CONF_SELECTED_CONTAINERS: [{"value": "ctr-paperless-app", "label": "paperless-app [paperless]"}],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_SELECTED_DISKS] == ["sda", "sdb"]
    assert defaults[CONF_SELECTED_FILESYSTEMS] == ["fs-1"]
    assert defaults[CONF_SELECTED_COMPOSE_PROJECTS] == ["paperless"]
    assert defaults[CONF_SELECTED_CONTAINERS] == ["ctr-paperless-app"]


@pytest.mark.asyncio
async def test_options_flow_does_not_auto_select_new_compose_resources_on_existing_entries(hass, config_entry) -> None:
    """Test newly introduced compose fields stay unselected on existing entries."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [{"value": "sda", "label": "sda"}],
                CONF_SELECTED_FILESYSTEMS: [{"value": "fs-1", "label": "data"}],
                CONF_SELECTED_SERVICES: [],
                CONF_SELECTED_NETWORK_INTERFACES: [],
                CONF_SELECTED_RAIDS: [],
                CONF_SELECTED_ZFS_POOLS: [],
                CONF_SELECTED_COMPOSE_PROJECTS: [{"value": "vaultwarden", "label": "vaultwarden (1)"}],
                CONF_SELECTED_CONTAINERS: [{"value": "ctr-vaultwarden", "label": "vaultwarden [vaultwarden]"}],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    assert defaults[CONF_SELECTED_COMPOSE_PROJECTS] == []
    assert defaults[CONF_SELECTED_CONTAINERS] == []


@pytest.mark.asyncio
async def test_options_flow_persists_missing_resource_fields(hass, config_entry) -> None:
    """Test missing multiselect fields do not clear persisted options."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_FILESYSTEMS: ["fs-1"],
            CONF_SELECTED_SERVICES: ["ssh"],
            CONF_SELECTED_NETWORK_INTERFACES: ["net-1"],
            CONF_SELECTED_RAIDS: ["md0"],
            CONF_SELECTED_ZFS_POOLS: ["tank"],
            CONF_SELECTED_COMPOSE_PROJECTS: ["paperless"],
            CONF_SELECTED_CONTAINERS: ["ctr-paperless-app"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                field: []
                for field in (
                    CONF_SELECTED_DISKS,
                    CONF_SELECTED_FILESYSTEMS,
                    CONF_SELECTED_SERVICES,
                    CONF_SELECTED_NETWORK_INTERFACES,
                    CONF_SELECTED_RAIDS,
                    CONF_SELECTED_ZFS_POOLS,
                    CONF_SELECTED_COMPOSE_PROJECTS,
                    CONF_SELECTED_CONTAINERS,
                )
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 120},
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SCAN_INTERVAL] == 120
    assert result["data"][CONF_SELECTED_DISKS] == ["sda"]
    assert result["data"][CONF_SELECTED_FILESYSTEMS] == ["fs-1"]
    assert result["data"][CONF_SELECTED_COMPOSE_PROJECTS] == ["paperless"]
    assert result["data"][CONF_SELECTED_CONTAINERS] == ["ctr-paperless-app"]


@pytest.mark.asyncio
async def test_options_flow_scan_interval_saves_correctly(hass, config_entry) -> None:
    """Test that the scan interval is persisted correctly."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                field: []
                for field in (
                    CONF_SELECTED_DISKS,
                    CONF_SELECTED_FILESYSTEMS,
                    CONF_SELECTED_SERVICES,
                    CONF_SELECTED_NETWORK_INTERFACES,
                    CONF_SELECTED_RAIDS,
                    CONF_SELECTED_ZFS_POOLS,
                    CONF_SELECTED_COMPOSE_PROJECTS,
                    CONF_SELECTED_CONTAINERS,
                )
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 300},
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SCAN_INTERVAL] == 300


@pytest.mark.asyncio
async def test_options_flow_cron_jobs_default_to_none(hass, config_entry) -> None:
    """Test the cron multiselect defaults to no selection (opt-in, never select-all)."""
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [{"value": "sda", "label": "sda"}],
                CONF_SELECTED_FILESYSTEMS: [],
                CONF_SELECTED_SERVICES: [],
                CONF_SELECTED_NETWORK_INTERFACES: [],
                CONF_SELECTED_RAIDS: [],
                CONF_SELECTED_ZFS_POOLS: [],
                CONF_SELECTED_COMPOSE_PROJECTS: [],
                CONF_SELECTED_CONTAINERS: [],
                CONF_SELECTED_CRON_JOBS: [
                    {"value": "cron-uuid-0001", "label": "Nightly cleanup"},
                    {"value": "cron-uuid-0002", "label": "Weekly task"},
                ],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})

    # Resource fields default to select-all on fresh entries; cron must not.
    assert defaults[CONF_SELECTED_DISKS] == ["sda"]
    assert defaults[CONF_SELECTED_CRON_JOBS] == []

    cron_marker = _field_marker(result["data_schema"], CONF_SELECTED_CRON_JOBS)
    cron_selector = result["data_schema"].schema[cron_marker]
    assert _selector_values(cron_selector) == ["cron-uuid-0001", "cron-uuid-0002"]


@pytest.mark.asyncio
async def test_options_flow_cron_jobs_round_trip_and_persist(hass, config_entry) -> None:
    """Test a persisted cron selection is the default and survives omission."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="OMV (nas)",
        data=config_entry.data,
        options={
            CONF_SELECTED_DISKS: ["sda"],
            CONF_SELECTED_CRON_JOBS: ["cron-uuid-0001"],
        },
    )
    config_entry.runtime_data = type(
        "RuntimeCoordinator",
        (),
        {
            "get_live_inventory": lambda self=None: {
                CONF_SELECTED_DISKS: [{"value": "sda", "label": "sda"}],
                CONF_SELECTED_FILESYSTEMS: [],
                CONF_SELECTED_SERVICES: [],
                CONF_SELECTED_NETWORK_INTERFACES: [],
                CONF_SELECTED_RAIDS: [],
                CONF_SELECTED_ZFS_POOLS: [],
                CONF_SELECTED_COMPOSE_PROJECTS: [],
                CONF_SELECTED_CONTAINERS: [],
                CONF_SELECTED_CRON_JOBS: [
                    {"value": "cron-uuid-0001", "label": "Nightly cleanup"},
                ],
            }
        },
    )()
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    defaults = result["data_schema"]({})
    assert defaults[CONF_SELECTED_CRON_JOBS] == ["cron-uuid-0001"]

    # Submitting without the field must not clear the persisted selection.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 120},
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SELECTED_CRON_JOBS] == ["cron-uuid-0001"]

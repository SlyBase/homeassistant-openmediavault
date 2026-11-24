"""Tests for the async OMV API client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web
from aioresponses import aioresponses
from aioresponses.core import CallbackResult
from homeassistant.helpers.aiohttp_client import DATA_CONNECTOR
from yarl import URL

from custom_components.omv.exceptions import (
    OMVAuthError,
    OMVConnectionError,
    OMVTimeoutError,
    OMVTwoFactorRequiredError,
)
from custom_components.omv.omv_api import OMVAPI


@pytest.fixture
def mock_aiohttp() -> aioresponses:
    """Yield an aiohttp response mocker."""
    with aioresponses() as mocked:
        yield mocked


@pytest.mark.asyncio
async def test_session_uses_ha_managed_connector(hass, mock_aiohttp: aioresponses) -> None:
    """Test the HTTP session is HA-managed (async_create_clientsession, Issue #133)."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"authenticated": True, "sessionid": "session123"},
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"version": "8.1.2", "hostname": "nas"},
            "error": None,
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    await api.async_connect()

    # HA-managed session: shares HA's connector pool (Issue #133).
    assert api._session.connector in hass.data.get(DATA_CONNECTOR, {}).values()
    await api.async_close()
    assert api._session is None


@pytest.mark.asyncio
async def test_login_success(hass, mock_aiohttp: aioresponses) -> None:
    """Test successful connect and initial system info fetch."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"authenticated": True, "sessionid": "session123"},
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"version": "8.1.2", "hostname": "nas"},
            "error": None,
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    info = await api.async_connect()

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_login_wrong_password(hass, mock_aiohttp: aioresponses) -> None:
    """Test authentication failure on login."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"authenticated": False}, "error": None},
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="wrong")
    with pytest.raises(OMVAuthError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_rpc_error_wrong_password_raises_auth_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test OMV login RPC errors for bad credentials surface as OMVAuthError."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": None,
            "error": {"code": 0, "message": "Incorrect username or password."},
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="wrong")
    with pytest.raises(OMVAuthError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_success_omv85_status_contract(hass, mock_aiohttp: aioresponses) -> None:
    """Test successful connect using OMV 8.5+'s status-based login contract."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "authenticated",
                "sessionid": "session123",
                "permissions": {},
                "username": "admin",
            },
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"version": "8.5.0", "hostname": "nas"},
            "error": None,
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    info = await api.async_connect()

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_login_challenge_required_raises_two_factor_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test that a 2FA challenge on login is surfaced as a distinguishable error."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "challengeRequired",
                "challenge": {"kind": "totp"},
                "username": "admin",
            },
            "error": None,
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVTwoFactorRequiredError) as excinfo:
        await api.async_connect()
    assert excinfo.value.challenge_kind == "totp"
    # Must still be catchable as a generic auth error by existing callers.
    assert isinstance(excinfo.value, OMVAuthError)
    await api.async_close()


@pytest.mark.asyncio
async def test_submit_two_factor_code_success(hass, mock_aiohttp: aioresponses) -> None:
    """Test completing a 2FA login via Session.verify."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "challengeRequired",
                "challenge": {"kind": "totp"},
                "username": "admin",
            },
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "authenticated",
                "sessionid": "session123",
                "username": "admin",
                "permissions": {},
            },
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"version": "8.5.0", "hostname": "nas"},
            "error": None,
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVTwoFactorRequiredError):
        await api.async_connect()

    info = await api.async_submit_two_factor_code("123456")

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_submit_two_factor_code_wrong_code_raises_auth_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test that a rejected verification code raises OMVAuthError."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "challengeRequired",
                "challenge": {"kind": "totp"},
                "username": "admin",
            },
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        status=401,
        payload={"response": None, "error": {"code": 0, "message": "Challenge verification failed."}},
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVTwoFactorRequiredError):
        await api.async_connect()

    with pytest.raises(OMVAuthError):
        await api.async_submit_two_factor_code("000000")
    await api.async_close()


TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.asyncio
async def test_connect_with_totp_secret_answers_challenge_automatically(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """A configured TOTP secret answers the login challenge via Session.verify."""
    seen_bodies: list[dict] = []

    def rpc_callback(url, **kwargs):
        body = kwargs.get("json") or {}
        seen_bodies.append(body)
        if body["method"] == "login":
            return CallbackResult(
                status=200,
                payload={
                    "response": {
                        "status": "challengeRequired",
                        "challenge": {"kind": "totp"},
                        "username": "admin",
                    },
                    "error": None,
                },
            )
        if body["method"] == "verify":
            return CallbackResult(
                status=200,
                payload={
                    "response": {
                        "status": "authenticated",
                        "sessionid": "session123",
                        "username": "admin",
                        "permissions": {},
                    },
                    "error": None,
                },
            )
        return CallbackResult(
            status=200,
            payload={"response": {"version": "8.5.0", "hostname": "nas"}, "error": None},
        )

    mock_aiohttp.post("http://192.168.1.1:80/rpc.php", callback=rpc_callback, repeat=True)

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass", totp_secret=TOTP_SECRET)
    info = await api.async_connect()

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    verify_bodies = [body for body in seen_bodies if body["method"] == "verify"]
    assert len(verify_bodies) == 1
    code = verify_bodies[0]["params"]["code"]
    assert isinstance(code, str) and len(code) == 6 and code.isdigit()
    await api.async_close()


@pytest.mark.asyncio
async def test_session_expiry_auto_relogin_with_totp_secret(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Session expiry on a 2FA account re-logins via the stored secret (Issue #55)."""
    # Initial connect: legacy login (no challenge yet) + System.getInformation.
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"authenticated": True, "sessionid": "session123"}, "error": None},
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"version": "8.5.0", "hostname": "nas"}, "error": None},
    )
    # RPC fails with session-expired, fresh login is challenged, verify succeeds,
    # the original RPC is retried successfully.
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": None, "error": {"code": 5001, "message": "expired"}},
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "challengeRequired",
                "challenge": {"kind": "totp"},
                "username": "admin",
            },
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "authenticated",
                "sessionid": "session456",
                "username": "admin",
                "permissions": {},
            },
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"cpuUtilization": 42}, "error": None},
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass", totp_secret=TOTP_SECRET)
    await api.async_connect()

    response = await api.async_call("System", "getInformation")

    assert response["cpuUtilization"] == 42
    assert api._session_id == "session456"
    await api.async_close()


@pytest.mark.asyncio
async def test_connect_with_wrong_totp_secret_raises_auth_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """All candidate codes rejected (wrong secret) surfaces as OMVAuthError."""

    def rpc_callback(url, **kwargs):
        body = kwargs.get("json") or {}
        if body["method"] == "login":
            return CallbackResult(
                status=200,
                payload={
                    "response": {
                        "status": "challengeRequired",
                        "challenge": {"kind": "totp"},
                        "username": "admin",
                    },
                    "error": None,
                },
            )
        return CallbackResult(
            status=401,
            payload={"response": None, "error": {"code": 0, "message": "Challenge verification failed."}},
        )

    mock_aiohttp.post("http://192.168.1.1:80/rpc.php", callback=rpc_callback, repeat=True)

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass", totp_secret=TOTP_SECRET)
    with pytest.raises(OMVAuthError) as excinfo:
        await api.async_connect()
    # Must NOT be the two-factor-required variant — a secret was configured,
    # it just did not work; a reauth flow (not a dead-end) is the right outcome.
    assert not isinstance(excinfo.value, OMVTwoFactorRequiredError)
    await api.async_close()


@pytest.mark.asyncio
async def test_connect_without_totp_secret_still_raises_two_factor_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Without a stored secret the challenge behaviour is unchanged."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {
                "status": "challengeRequired",
                "challenge": {"kind": "totp"},
                "username": "admin",
            },
            "error": None,
        },
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVTwoFactorRequiredError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_opaque_401_raises_connection_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test a bare, non-OMV-RPC 401 (e.g. from a proxy/WAF) is not treated as invalid credentials."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        status=401,
        body="Unauthorized",
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVConnectionError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_omv_rpc_401_raises_auth_error(hass, mock_aiohttp: aioresponses) -> None:
    """Test that a genuine OMV RPC error body on a 401 still raises OMVAuthError."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        status=401,
        payload={"response": None, "error": {"code": 5001, "message": "Session expired"}},
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVAuthError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_session_expiry_auto_reauth(hass, mock_aiohttp: aioresponses) -> None:
    """Test automatic reauthentication when the session expires."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"authenticated": True, "sessionid": "session123"},
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"version": "8.1.2", "hostname": "nas"},
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": None, "error": {"code": 5001, "message": "expired"}},
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"authenticated": True, "sessionid": "session456"},
            "error": None,
        },
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"cpuUtilization": 42}, "error": None},
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    await api.async_connect()

    response = await api.async_call("System", "getInformation")

    assert response["cpuUtilization"] == 42
    assert api._session_id == "session456"
    await api.async_close()


@pytest.mark.asyncio
async def test_login_uses_session_id_header_for_follow_up_calls(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test follow-up RPCs include the OMV session header from login."""
    seen_headers: list[dict[str, str]] = []

    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={
            "response": {"authenticated": True, "sessionid": "session123"},
            "error": None,
        },
    )

    def system_callback(url, **kwargs):
        headers = kwargs.get("headers") or {}
        seen_headers.append(headers)
        return CallbackResult(
            status=200,
            payload={"response": {"hostname": "nas"}, "error": None},
        )

    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        callback=system_callback,
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    await api.async_connect()

    # HA-managed sessions inject a default User-Agent; the OMV session header
    # must be present (exact match would couple to HA's default headers).
    assert seen_headers
    assert seen_headers[0]["X-OPENMEDIAVAULT-SESSIONID"] == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_connection_error_raises_omv_connection_error(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test network errors surface as OMVConnectionError."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        exception=aiohttp.ClientConnectionError("boom"),
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    with pytest.raises(OMVConnectionError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_session_accepts_cookies_for_ip_hosts(hass) -> None:
    """Test the session cookie jar retains OMV cookies for IP-based hosts."""
    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")

    await api._async_ensure_session()

    assert api._session is not None
    api._session.cookie_jar.update_cookies(
        {"X-OPENMEDIAVAULT-SESSIONID": "session123"},
        response_url=URL("http://192.168.1.1:80/rpc.php"),
    )

    cookies = api._session.cookie_jar.filter_cookies(URL("http://192.168.1.1:80/rpc.php"))

    assert cookies["X-OPENMEDIAVAULT-SESSIONID"].value == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_async_close_detaches_ha_managed_session(hass) -> None:
    """Test async_close detaches (does not close) the HA-managed session."""
    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    await api._async_ensure_session()
    session = api._session
    assert session is not None

    await api.async_close()

    assert api._session is None
    assert api._session_id is None
    # detach() marks the session closed without touching the shared connector.
    assert session.closed
    assert not any(c.closed for c in hass.data.get(DATA_CONNECTOR, {}).values())


@pytest.mark.asyncio
async def test_async_apply_config_sends_required_params(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """Test async_apply_config passes modules+force params required by OMV 8."""
    seen_bodies: list[dict] = []

    def apply_callback(url, **kwargs):
        seen_bodies.append(kwargs.get("json") or {})
        return CallbackResult(
            status=200,
            payload={"response": ["nginx"], "error": None},
        )

    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        callback=apply_callback,
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    api._session_id = "sess1"
    await api._async_ensure_session()
    await api.async_apply_config()

    assert len(seen_bodies) == 1
    body = seen_bodies[0]
    assert body["service"] == "Config"
    assert body["method"] == "applyChanges"
    assert body["params"] == {"modules": [], "force": False}
    await api.async_close()


def _login_cookie_names(api: OMVAPI) -> list[str]:
    """Return the cookie names currently stored for the OMV RPC endpoint."""
    assert api._session is not None
    cookies = api._session.cookie_jar.filter_cookies(URL(f"{api.base_url}/rpc.php"))
    return sorted(cookies.keys())


@pytest.mark.asyncio
async def test_get_set_login_cookie_name_roundtrip(hass) -> None:
    """The login-notification cookie name getter/setter round-trips (Issue #62)."""
    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    assert api.get_login_cookie_name() is None
    api.set_login_cookie_name("OPENMEDIAVAULT-LOGIN-abcd")
    assert api.get_login_cookie_name() == "OPENMEDIAVAULT-LOGIN-abcd"


@pytest.mark.asyncio
async def test_capture_login_cookie_records_prefixed_cookie(hass) -> None:
    """_capture_login_cookie records the OMV login-dedup cookie, ignoring others."""
    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    await api._async_ensure_session()
    assert api._session is not None
    # Simulate what real aiohttp does when OMV sends the Set-Cookie header on
    # the first login from a new browser, plus an unrelated session cookie.
    api._session.cookie_jar.update_cookies(
        {
            "X-OPENMEDIAVAULT-SESSIONID": "sess-value",
            "OPENMEDIAVAULT-LOGIN-deadbeef": "dune-quote",
        },
        response_url=URL("http://192.168.1.1:80/rpc.php"),
    )

    api._capture_login_cookie()

    assert api.get_login_cookie_name() == "OPENMEDIAVAULT-LOGIN-deadbeef"
    await api.async_close()


@pytest.mark.asyncio
async def test_ensure_session_reinjects_login_cookie(hass) -> None:
    """Recreating the session replays the login-dedup cookie so it survives.

    The connection-error retry path recreates the aiohttp session; without
    re-injection the OMV login-notification cookie would be lost and OMV would
    email again on the next login (Issue #62).
    """
    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    api.set_login_cookie_name("OPENMEDIAVAULT-LOGIN-seed")

    await api._async_ensure_session()
    assert "OPENMEDIAVAULT-LOGIN-seed" in _login_cookie_names(api)

    # A second recreation (as happens during connection-error recovery) must
    # still carry the cookie.
    await api._async_ensure_session()
    assert "OPENMEDIAVAULT-LOGIN-seed" in _login_cookie_names(api)
    await api.async_close()


@pytest.mark.asyncio
async def test_seeded_login_cookie_present_for_first_login(
    hass,
    mock_aiohttp: aioresponses,
) -> None:
    """A persisted cookie name is replayed into the jar before the first login.

    This is what suppresses OMV's repeated login-notification email after an
    HA restart: the cookie is already in the jar when ``Session.login`` is
    sent (Issue #62).
    """
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"authenticated": True, "sessionid": "session123"}, "error": None},
    )
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"version": "8.1.2", "hostname": "nas"}, "error": None},
    )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="pass")
    api.set_login_cookie_name("OPENMEDIAVAULT-LOGIN-restored")
    await api.async_connect()

    # After connect the restored cookie is still in the jar (was sent with the
    # login request) and remains captured for persistence.
    assert "OPENMEDIAVAULT-LOGIN-restored" in _login_cookie_names(api)
    assert api.get_login_cookie_name() == "OPENMEDIAVAULT-LOGIN-restored"
    await api.async_close()


def _rpc_request_kwargs(mock_aiohttp: aioresponses) -> list[dict]:
    """Return the kwargs of every recorded POST to the OMV RPC endpoint."""
    return [call.kwargs for call in mock_aiohttp.requests[("POST", URL("http://192.168.1.1:80/rpc.php"))]]


@pytest.mark.asyncio
async def test_async_call_passes_per_call_timeout(hass, mock_aiohttp: aioresponses) -> None:
    """A per-call timeout bounds the read per attempt; connect keeps _REQUEST_TIMEOUT; default is untouched."""
    for _ in range(2):
        mock_aiohttp.post(
            "http://192.168.1.1:80/rpc.php",
            payload={"response": None, "error": None},
        )

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="***")
    api._session_id = "sess1"
    await api._async_ensure_session()
    await api.async_call(
        "Kubectl",
        "delete",
        {"type": "pod", "name": "web-1", "namespace": "web-app"},
        max_retries=0,
        timeout=60.0,
    )
    await api.async_call("System", "getInformation")

    with_timeout, without_timeout = _rpc_request_kwargs(mock_aiohttp)
    assert with_timeout["timeout"] == aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=60.0)
    assert with_timeout["json"]["params"] == {"type": "pod", "name": "web-1", "namespace": "web-app"}
    # No override: no per-request timeout, so the session default (30 s) applies.
    assert "timeout" not in without_timeout
    await api.async_close()


@pytest.mark.asyncio
async def test_async_call_read_timeout_on_real_socket_raises_omv_timeout_error(hass, socket_enabled) -> None:
    """A real sock_read timeout after the request was sent raises OMVTimeoutError.

    Uses a local aiohttp server that accepts the request and never answers in
    time, so the installed aiohttp raises its genuine read-timeout exception
    (``aiohttp.SocketTimeoutError``). With ``max_retries=0`` the type and the
    original exception are preserved instead of being wrapped.
    """
    received = asyncio.Event()
    release = asyncio.Event()

    async def slow_rpc(request: web.Request) -> web.Response:
        """Record the request, then hold the response past the read timeout."""
        await request.read()
        received.set()
        await release.wait()
        return web.json_response({"response": None, "error": None})

    app = web.Application()
    app.router.add_post("/rpc.php", slow_rpc)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    api = OMVAPI(hass=hass, host="127.0.0.1", username="admin", password="***", port=port)
    api._session_id = "sess1"
    await api._async_ensure_session()
    try:
        with pytest.raises(OMVTimeoutError) as exc_info:
            await api.async_call("Kubectl", "delete", {"type": "pod", "name": "p"}, max_retries=0, timeout=0.2)
    finally:
        release.set()
        await api.async_close()
        await runner.cleanup()

    assert received.is_set()
    assert isinstance(exc_info.value.__cause__, aiohttp.SocketTimeoutError)
    assert "Failed to reach OMV" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_call_connect_timeout_stays_plain_connection_error(hass, mock_aiohttp: aioresponses) -> None:
    """A connect timeout (nothing was sent) is a plain OMVConnectionError, not OMVTimeoutError."""
    mock_aiohttp.post("http://192.168.1.1:80/rpc.php", exception=aiohttp.ConnectionTimeoutError("connect"))
    mock_aiohttp.post("http://192.168.1.1:80/rpc.php", exception=TimeoutError())

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="***")
    api._session_id = "sess1"
    await api._async_ensure_session()

    for _ in range(2):
        with pytest.raises(OMVConnectionError) as exc_info:
            await api.async_call("Kubectl", "delete", {"type": "pod", "name": "p"}, max_retries=0, timeout=10)
        assert not isinstance(exc_info.value, OMVTimeoutError)
    await api.async_close()


@pytest.mark.asyncio
async def test_async_call_read_timeout_type_survives_retries(hass, mock_aiohttp: aioresponses) -> None:
    """With retries, a read timeout on the last attempt still surfaces as OMVTimeoutError."""
    mock_aiohttp.post("http://192.168.1.1:80/rpc.php", exception=aiohttp.SocketTimeoutError("read"))
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"authenticated": True, "sessionid": "s2"}, "error": None},
    )
    mock_aiohttp.post("http://192.168.1.1:80/rpc.php", exception=aiohttp.SocketTimeoutError("read"))

    api = OMVAPI(hass=hass, host="192.168.1.1", username="admin", password="***")
    api._session_id = "sess1"
    await api._async_ensure_session()

    with (
        patch("custom_components.omv.omv_api.asyncio.sleep", new=AsyncMock()),
        pytest.raises(OMVTimeoutError) as exc_info,
    ):
        await api.async_call("System", "getInformation", max_retries=1, timeout=5)

    assert "Failed to reach OMV after 1 retries" in str(exc_info.value)
    await api.async_close()

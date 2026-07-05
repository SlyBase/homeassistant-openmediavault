"""Tests for the async OMV API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses.core import CallbackResult
from yarl import URL

from custom_components.omv.exceptions import (
    OMVAuthError,
    OMVConnectionError,
    OMVTwoFactorRequiredError,
)
from custom_components.omv.omv_api import OMVAPI


@pytest.fixture
def mock_aiohttp() -> aioresponses:
    """Yield an aiohttp response mocker."""
    with aioresponses() as mocked:
        yield mocked


@pytest.mark.asyncio
async def test_login_success(mock_aiohttp: aioresponses) -> None:
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    info = await api.async_connect()

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_login_wrong_password(mock_aiohttp: aioresponses) -> None:
    """Test authentication failure on login."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        payload={"response": {"authenticated": False}, "error": None},
    )

    api = OMVAPI("192.168.1.1", "admin", "wrong")
    with pytest.raises(OMVAuthError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_rpc_error_wrong_password_raises_auth_error(
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

    api = OMVAPI("192.168.1.1", "admin", "wrong")
    with pytest.raises(OMVAuthError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_success_omv85_status_contract(mock_aiohttp: aioresponses) -> None:
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    info = await api.async_connect()

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_login_challenge_required_raises_two_factor_error(
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVTwoFactorRequiredError) as excinfo:
        await api.async_connect()
    assert excinfo.value.challenge_kind == "totp"
    # Must still be catchable as a generic auth error by existing callers.
    assert isinstance(excinfo.value, OMVAuthError)
    await api.async_close()


@pytest.mark.asyncio
async def test_submit_two_factor_code_success(mock_aiohttp: aioresponses) -> None:
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVTwoFactorRequiredError):
        await api.async_connect()

    info = await api.async_submit_two_factor_code("123456")

    assert info["hostname"] == "nas"
    assert api._session_id == "session123"
    await api.async_close()


@pytest.mark.asyncio
async def test_submit_two_factor_code_wrong_code_raises_auth_error(
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVTwoFactorRequiredError):
        await api.async_connect()

    with pytest.raises(OMVAuthError):
        await api.async_submit_two_factor_code("000000")
    await api.async_close()


TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.asyncio
async def test_connect_with_totp_secret_answers_challenge_automatically(
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

    api = OMVAPI("192.168.1.1", "admin", "pass", totp_secret=TOTP_SECRET)
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

    api = OMVAPI("192.168.1.1", "admin", "pass", totp_secret=TOTP_SECRET)
    await api.async_connect()

    response = await api.async_call("System", "getInformation")

    assert response["cpuUtilization"] == 42
    assert api._session_id == "session456"
    await api.async_close()


@pytest.mark.asyncio
async def test_connect_with_wrong_totp_secret_raises_auth_error(
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

    api = OMVAPI("192.168.1.1", "admin", "pass", totp_secret=TOTP_SECRET)
    with pytest.raises(OMVAuthError) as excinfo:
        await api.async_connect()
    # Must NOT be the two-factor-required variant — a secret was configured,
    # it just did not work; a reauth flow (not a dead-end) is the right outcome.
    assert not isinstance(excinfo.value, OMVTwoFactorRequiredError)
    await api.async_close()


@pytest.mark.asyncio
async def test_connect_without_totp_secret_still_raises_two_factor_error(
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVTwoFactorRequiredError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_opaque_401_raises_connection_error(
    mock_aiohttp: aioresponses,
) -> None:
    """Test a bare, non-OMV-RPC 401 (e.g. from a proxy/WAF) is not treated as invalid credentials."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        status=401,
        body="Unauthorized",
    )

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVConnectionError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_login_omv_rpc_401_raises_auth_error(mock_aiohttp: aioresponses) -> None:
    """Test that a genuine OMV RPC error body on a 401 still raises OMVAuthError."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        status=401,
        payload={"response": None, "error": {"code": 5001, "message": "Session expired"}},
    )

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVAuthError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_session_expiry_auto_reauth(mock_aiohttp: aioresponses) -> None:
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    await api.async_connect()

    response = await api.async_call("System", "getInformation")

    assert response["cpuUtilization"] == 42
    assert api._session_id == "session456"
    await api.async_close()


@pytest.mark.asyncio
async def test_login_uses_session_id_header_for_follow_up_calls(
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    await api.async_connect()

    assert seen_headers == [{"X-OPENMEDIAVAULT-SESSIONID": "session123"}]
    await api.async_close()


@pytest.mark.asyncio
async def test_connection_error_raises_omv_connection_error(
    mock_aiohttp: aioresponses,
) -> None:
    """Test network errors surface as OMVConnectionError."""
    mock_aiohttp.post(
        "http://192.168.1.1:80/rpc.php",
        exception=aiohttp.ClientConnectionError("boom"),
    )

    api = OMVAPI("192.168.1.1", "admin", "pass")
    with pytest.raises(OMVConnectionError):
        await api.async_connect()
    await api.async_close()


@pytest.mark.asyncio
async def test_session_accepts_cookies_for_ip_hosts() -> None:
    """Test the session cookie jar retains OMV cookies for IP-based hosts."""
    api = OMVAPI("192.168.1.1", "admin", "pass")

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
async def test_async_close_yields_for_aiohttp_cleanup() -> None:
    """Test async_close yields one loop tick for aiohttp transport cleanup."""
    api = OMVAPI("192.168.1.1", "admin", "pass")
    await api._async_ensure_session()

    with patch("custom_components.omv.omv_api.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await api.async_close()

    sleep_mock.assert_awaited_once_with(0)
    assert api._session is None
    assert api._session_id is None


@pytest.mark.asyncio
async def test_async_apply_config_sends_required_params(
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

    api = OMVAPI("192.168.1.1", "admin", "pass")
    api._session_id = "sess1"
    await api._async_ensure_session()
    await api.async_apply_config()

    assert len(seen_bodies) == 1
    body = seen_bodies[0]
    assert body["service"] == "Config"
    assert body["method"] == "applyChanges"
    assert body["params"] == {"modules": [], "force": False}
    await api.async_close()

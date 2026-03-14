"""Tests for the async OMV API client."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses.core import CallbackResult
from yarl import URL

from custom_components.omv.exceptions import OMVAuthError, OMVConnectionError
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

    cookies = api._session.cookie_jar.filter_cookies(
        URL("http://192.168.1.1:80/rpc.php")
    )

    assert cookies["X-OPENMEDIAVAULT-SESSIONID"].value == "session123"
    await api.async_close()

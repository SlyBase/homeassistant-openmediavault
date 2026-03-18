"""Async OpenMediaVault JSON-RPC API client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from yarl import URL

from .exceptions import OMVApiError, OMVAuthError, OMVConnectionError

_LOGGER = logging.getLogger(__name__)
_SESSION_EXPIRED_CODES = {5001, 5002}
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_INVALID_LOGIN_MESSAGES = (
    "incorrect username or password",
    "invalid username or password",
    "authentication failed",
)


class OMVAPI:
    """Async client for the OMV JSON-RPC endpoint."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 80,
        ssl: bool = False,
        verify_ssl: bool = True,
        source: str = "runtime",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._ssl = ssl
        self._verify_ssl = verify_ssl
        self._source = source
        self._session_id: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the configured OMV base URL."""
        scheme = "https" if self._ssl else "http"
        return f"{scheme}://{self._host}:{self._port}"

    async def async_connect(self) -> dict[str, Any]:
        """Create a session, authenticate and return system information."""
        _LOGGER.debug(
            "Starting OMV connect [%s] host=%r port=%s ssl=%s verify_ssl=%s "
            "username=%r username_has_outer_whitespace=%s password_length=%d "
            "password_has_outer_whitespace=%s",
            self._source,
            self._host,
            self._port,
            self._ssl,
            self._verify_ssl,
            self._username,
            self._has_outer_whitespace(self._username),
            len(self._password),
            self._has_outer_whitespace(self._password),
        )
        await self._async_ensure_session()
        await self._async_login()
        response = await self.async_call("System", "getInformation")
        return response if isinstance(response, dict) else {}

    @staticmethod
    def _has_outer_whitespace(value: str) -> bool:
        """Return whether a credential contains leading or trailing whitespace."""
        return value != value.strip()

    def _cookie_names(self) -> list[str]:
        """Return the active cookie names for the current OMV endpoint."""
        if not self._session or self._session.closed:
            return []

        cookies = self._session.cookie_jar.filter_cookies(URL(f"{self.base_url}/rpc.php"))
        return sorted(cookies.keys())

    @staticmethod
    def _is_invalid_login_message(message: str) -> bool:
        """Return whether the OMV error message represents invalid credentials."""
        lowered = message.casefold()
        return any(fragment in lowered for fragment in _INVALID_LOGIN_MESSAGES)

    async def _async_ensure_session(self) -> None:
        """Create or recreate the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

        connector = aiohttp.TCPConnector(ssl=self._verify_ssl if self._ssl else False)
        cookie_jar = aiohttp.CookieJar(unsafe=True)
        self._session = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=cookie_jar,
            timeout=_REQUEST_TIMEOUT,
        )

    async def _async_login(self) -> None:
        """Authenticate with OMV and initialize the session cookie jar."""
        self._session_id = None
        data = await self._async_raw_call(
            "session",
            "login",
            {"username": self._username, "password": self._password},
            options=None,
        )
        if not isinstance(data, dict) or not data.get("authenticated"):
            _LOGGER.debug(
                "OMV login rejected [%s] host=%r authenticated=%s sessionid_present=%s cookie_names=%s",
                self._source,
                self._host,
                isinstance(data, dict) and data.get("authenticated"),
                isinstance(data, dict) and bool(data.get("sessionid")),
                self._cookie_names(),
            )
            raise OMVAuthError("Invalid credentials")

        session_id = data.get("sessionid")
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id

        _LOGGER.debug(
            "Successfully authenticated with OMV at %s [%s]; sessionid_present=%s cookie_names=%s",
            self._host,
            self._source,
            self._session_id is not None,
            self._cookie_names(),
        )

    async def async_call(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a JSON-RPC call and recover from session expiry."""
        async with self._lock:
            try:
                return await self._async_raw_call(service, method, params)
            except OMVAuthError:
                _LOGGER.debug(
                    "Session expired for %s [%s] during %s.%s, re-authenticating",
                    self._host,
                    self._source,
                    service,
                    method,
                )
                await self._async_login()
                return await self._async_raw_call(service, method, params)

    async def _async_raw_call(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a raw JSON-RPC call against OMV."""
        if not self._session or self._session.closed:
            raise OMVConnectionError("No active OMV session")

        headers = None
        if self._session_id:
            headers = {"X-OPENMEDIAVAULT-SESSIONID": self._session_id}

        _LOGGER.debug(
            "OMV RPC request [%s] %s.%s host=%r has_session_header=%s cookie_names=%s param_keys=%s",
            self._source,
            service,
            method,
            self._host,
            headers is not None,
            self._cookie_names(),
            sorted((params or {}).keys()),
        )

        payload: dict[str, Any] = {
            "service": service,
            "method": method,
            "params": params or {},
        }
        if options is not None:
            payload["options"] = options
        elif service != "session":
            payload["options"] = {"updatelastaccess": True}

        try:
            async with self._session.post(
                f"{self.base_url}/rpc.php",
                headers=headers,
                json=payload,
            ) as response:
                if response.status in (401, 403):
                    _LOGGER.debug(
                        "OMV RPC HTTP auth failure [%s] %s.%s host=%r status=%s has_session_header=%s cookie_names=%s",
                        self._source,
                        service,
                        method,
                        self._host,
                        response.status,
                        headers is not None,
                        self._cookie_names(),
                    )
                    raise OMVAuthError(f"OMV returned HTTP {response.status}")
                if response.status >= 500:
                    _LOGGER.debug(
                        "OMV RPC HTTP server failure [%s] %s.%s host=%r status=%s "
                        "has_session_header=%s cookie_names=%s",
                        self._source,
                        service,
                        method,
                        self._host,
                        response.status,
                        headers is not None,
                        self._cookie_names(),
                    )
                    raise OMVConnectionError(f"OMV returned HTTP {response.status}")
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OMVConnectionError(f"Connection to {self._host} failed: {err}") from err
        except ValueError as err:
            raise OMVConnectionError(f"Invalid JSON response from {self._host}: {err}") from err

        if error := data.get("error"):
            code = error.get("code", 0)
            message = error.get("message", "Unknown error")
            _LOGGER.debug(
                "OMV RPC error [%s] %s.%s host=%r code=%s message=%r "
                "has_session_header=%s sessionid_present=%s cookie_names=%s",
                self._source,
                service,
                method,
                self._host,
                code,
                message,
                headers is not None,
                self._session_id is not None,
                self._cookie_names(),
            )
            if service == "session" and method == "login" and self._is_invalid_login_message(message):
                raise OMVAuthError(message)
            if code in _SESSION_EXPIRED_CODES:
                raise OMVAuthError(message)
            raise OMVApiError(f"RPC {service}.{method} failed: {message}")

        _LOGGER.debug(
            "OMV RPC response [%s] %s.%s host=%r ok response_type=%s "
            "has_session_header=%s sessionid_present=%s cookie_names=%s",
            self._source,
            service,
            method,
            self._host,
            type(data.get("response")).__name__,
            headers is not None,
            self._session_id is not None,
            self._cookie_names(),
        )

        return data.get("response")

    async def async_close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._session_id = None

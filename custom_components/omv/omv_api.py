"""Async OpenMediaVault JSON-RPC API client."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from yarl import URL

from . import totp
from .exceptions import (
    OMVApiError,
    OMVAuthError,
    OMVConnectionError,
    OMVTwoFactorRequiredError,
)

_LOGGER = logging.getLogger(__name__)
_SESSION_EXPIRED_CODES = {5001, 5002}
# OMV sets a persistent (60-day) browser-dedup cookie named
# ``OPENMEDIAVAULT-LOGIN-<hex(bcrypt(username))>`` on the first login from a
# given browser and emails a security notification only when it is absent
# (see openmediavault ``rpc/session.inc::handleSuccessfulLogin``). Persisting
# and replaying this cookie makes OMV treat the integration like the same
# browser, so the login-notification email is not re-sent on every re-login /
# HA restart (Issue #62). Only the cookie *name* is checked by OMV, not its
# value.
_LOGIN_NOTIFICATION_COOKIE_PREFIX = "OPENMEDIAVAULT-LOGIN-"
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
        totp_secret: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._ssl = ssl
        self._verify_ssl = verify_ssl
        self._source = source
        self._totp_secret = totp_secret
        self._session_id: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._login_cookie_name: str | None = None

    def set_totp_secret(self, totp_secret: str | None) -> None:
        """Set the TOTP secret used to answer 2FA challenges automatically.

        Needed by the config flow, which learns the secret only after the
        API instance was created (in the ``totp`` step) but hands exactly
        this instance off to the runtime setup afterwards.

        Args:
            totp_secret: Base32-encoded shared secret, or None to disable
                automatic challenge answering.
        """
        self._totp_secret = totp_secret

    def get_login_cookie_name(self) -> str | None:
        """Return the OMV login-notification dedup cookie name, if known.

        Returns:
            The ``OPENMEDIAVAULT-LOGIN-*`` cookie name captured from a previous
            successful login, or ``None`` if it has not been observed yet.
            Used by the config-entry setup to persist the name across HA
            restarts (Issue #62).
        """
        return self._login_cookie_name

    def set_login_cookie_name(self, name: str | None) -> None:
        """Seed the OMV login-notification dedup cookie name.

        Called before :meth:`async_connect` with a value persisted from a
        prior HA run so the very first ``Session.login`` after a restart
        replays the cookie and OMV suppresses the login-notification email
        (Issue #62). The cookie is injected into the jar on the next session
        (re)creation via :meth:`_inject_login_cookie`.

        Args:
            name: The ``OPENMEDIAVAULT-LOGIN-*`` cookie name, or ``None``.
        """
        self._login_cookie_name = name

    @property
    def base_url(self) -> str:
        """Return the configured OMV base URL."""
        scheme = "https" if self._ssl else "http"
        return f"{scheme}://{self._host}:{self._port}"

    async def async_connect(self) -> dict[str, Any]:
        """Create a session, authenticate and return system information."""
        _LOGGER.debug(
            "Starting OMV connect [%s] host=%r port=%s ssl=%s verify_ssl=%s "
            "username=%r username_has_outer_whitespace=%s "
            "password_has_outer_whitespace=%s",
            self._source,
            self._host,
            self._port,
            self._ssl,
            self._verify_ssl,
            self._username,
            self._has_outer_whitespace(self._username),
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
        """Create or recreate the aiohttp session.

        Re-injects the OMV login-notification dedup cookie (if known) into the
        fresh cookie jar so recreating the session — e.g. on the connection
        error retry path — does not drop it and cause OMV to re-send the login
        notification email (Issue #62).
        """
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=_REQUEST_TIMEOUT,
        )
        self._inject_login_cookie()

    def _inject_login_cookie(self) -> None:
        """Replay the OMV login-notification dedup cookie into the current jar.

        OMV only checks the cookie *name* (``OPENMEDIAVAULT-LOGIN-*``), so a
        placeholder value is sufficient. The cookie is added without a
        ``SameSite`` attribute so aiohttp always sends it on the
        ``POST /rpc.php`` requests, independent of same-site heuristics that
        could otherwise suppress OMV's own ``SameSite=Strict`` cookie
        (Issue #62).
        """
        if not self._login_cookie_name or not self._session or self._session.closed:
            return
        self._session.cookie_jar.update_cookies(
            {self._login_cookie_name: "1"},
            response_url=URL(self.base_url),
        )

    def _capture_login_cookie(self) -> None:
        """Capture the OMV login-notification dedup cookie name from the jar.

        Scans the active cookie jar for a cookie whose name starts with
        :data:`_LOGIN_NOTIFICATION_COOKIE_PREFIX` (set by OMV on the first
        login from a new browser) and records it so it can be persisted and
        replayed on later logins / HA restarts (Issue #62). Existing values
        are only overwritten when OMV issued a (new) cookie.
        """
        if not self._session or self._session.closed:
            return
        cookies = self._session.cookie_jar.filter_cookies(URL(f"{self.base_url}/rpc.php"))
        for name in cookies:
            if name.upper().startswith(_LOGIN_NOTIFICATION_COOKIE_PREFIX):
                self._login_cookie_name = name
                _LOGGER.debug(
                    "Captured OMV login-notification cookie [%s] host=%r name=%r",
                    self._source,
                    self._host,
                    name,
                )
                return

    async def _async_login(self) -> None:
        """Authenticate with OMV and initialize the session cookie jar.

        Handles two ``Session.login`` response shapes:

        - Legacy (OMV 7, pre-2FA OMV 8): ``{"authenticated": true, "sessionid": ...}``.
        - Current (OMV 8.5+, native TOTP 2FA support): ``{"status": "authenticated",
          "sessionid": ...}`` on success, or ``{"status": "challengeRequired", ...}``
          when the account has two-factor authentication enabled. When a TOTP
          secret is configured, a ``totp`` challenge is answered automatically
          via :meth:`_async_answer_totp_challenge` (Issue #55) so session
          expiry and HA restarts don't dead-end in a challenge nobody can
          answer. Without a secret, the challenge raises
          :class:`~custom_components.omv.exceptions.OMVTwoFactorRequiredError`
          so callers (e.g. the config flow) can complete it via
          :meth:`async_submit_two_factor_code`.

        Raises:
            OMVAuthError: If the credentials are rejected, or a TOTP challenge
                could not be answered with the configured secret.
            OMVTwoFactorRequiredError: If the account requires two-factor
                authentication and no TOTP secret is configured (or the
                challenge is of an unsupported kind).
        """
        self._session_id = None
        data = await self._async_raw_call(
            "session",
            "login",
            {"username": self._username, "password": self._password},
            options=None,
        )
        status = data.get("status") if isinstance(data, dict) else None
        if status is not None:
            authenticated = status == "authenticated"
        else:
            authenticated = isinstance(data, dict) and bool(data.get("authenticated"))

        if not authenticated:
            _LOGGER.debug(
                "OMV login rejected [%s] host=%r status=%r authenticated=%s sessionid_present=%s cookie_names=%s",
                self._source,
                self._host,
                status,
                isinstance(data, dict) and data.get("authenticated"),
                isinstance(data, dict) and bool(data.get("sessionid")),
                self._cookie_names(),
            )
            if status == "challengeRequired":
                challenge_kind = (
                    isinstance(data, dict) and isinstance(data.get("challenge"), dict) and data["challenge"].get("kind")
                )
                if challenge_kind == "totp" and self._totp_secret:
                    data = await self._async_answer_totp_challenge()
                    session_id = data.get("sessionid")
                    if isinstance(session_id, str) and session_id:
                        self._session_id = session_id
                    self._capture_login_cookie()
                    _LOGGER.debug(
                        "Automatically answered OMV TOTP challenge for %s [%s]; sessionid_present=%s",
                        self._host,
                        self._source,
                        self._session_id is not None,
                    )
                    return
                raise OMVTwoFactorRequiredError(
                    f"OMV account requires two-factor authentication (challenge kind={challenge_kind!r})",
                    challenge_kind=challenge_kind if isinstance(challenge_kind, str) else None,
                )
            raise OMVAuthError("Invalid credentials")

        session_id = data.get("sessionid")
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id

        self._capture_login_cookie()
        _LOGGER.debug(
            "Successfully authenticated with OMV at %s [%s]; sessionid_present=%s cookie_names=%s",
            self._host,
            self._source,
            self._session_id is not None,
            self._cookie_names(),
        )

    async def _async_answer_totp_challenge(self) -> dict[str, Any]:
        """Answer a pending TOTP challenge with the configured secret.

        Generates the RFC 6238 code locally and submits it via
        ``Session.verify`` on the same aiohttp session — OMV binds the pending
        login to the PHP session cookie set by ``Session.login``. If the
        current-window code is rejected, the neighbouring 30-second windows
        (±``totp.TIME_STEP``) are tried once each to tolerate clock skew
        between the HA host and the NAS.

        Returns:
            The successful ``Session.verify`` response
            (``{"status": "authenticated", "sessionid": ...}``).

        Raises:
            OMVAuthError: When every candidate code is rejected (wrong secret,
                or the pending login already expired server-side).
            OMVConnectionError: When the OMV host is unreachable.
        """
        assert self._totp_secret is not None
        now = time.time()
        candidates: list[str] = []
        for offset in (0, -totp.TIME_STEP, totp.TIME_STEP):
            code = totp.generate_code(self._totp_secret, now + offset)
            if code not in candidates:
                candidates.append(code)

        last_err: OMVAuthError | None = None
        for attempt, code in enumerate(candidates):
            try:
                data = await self._async_raw_call(
                    "session",
                    "verify",
                    {"code": code},
                    options=None,
                )
            except OMVAuthError as err:
                last_err = err
                _LOGGER.debug(
                    "OMV TOTP challenge answer rejected [%s] host=%r attempt=%d/%d",
                    self._source,
                    self._host,
                    attempt + 1,
                    len(candidates),
                )
                continue
            status = data.get("status") if isinstance(data, dict) else None
            if status == "authenticated":
                return data if isinstance(data, dict) else {}
            last_err = OMVAuthError(f"Two-factor verification failed (status={status!r})")

        raise OMVAuthError("Two-factor verification with the stored TOTP secret failed") from last_err

    async def async_submit_two_factor_code(self, code: str) -> dict[str, Any]:
        """Complete a two-step OMV login by submitting the second-factor code.

        Must be called on the same :class:`OMVAPI` instance that received the
        ``challengeRequired`` response from :meth:`async_connect`: OMV binds
        the in-progress login to the PHP session cookie set during the first
        step (``Session.login``), so the same aiohttp session/cookie jar has
        to be reused for ``Session.verify``.

        Args:
            code: The verification code (e.g. a 6-digit TOTP code).

        Returns:
            The ``System.getInformation`` response.

        Raises:
            OMVAuthError: If the code is rejected or the pending login
                (server-side, 5-minute TTL) has expired.
        """
        self._session_id = None
        data = await self._async_raw_call(
            "session",
            "verify",
            {"code": code},
            options=None,
        )
        status = data.get("status") if isinstance(data, dict) else None
        if status != "authenticated":
            raise OMVAuthError("Two-factor verification failed")

        session_id = data.get("sessionid")
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id

        self._capture_login_cookie()
        response = await self.async_call("System", "getInformation")
        return response if isinstance(response, dict) else {}

    async def async_call(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        max_retries: int = 3,
    ) -> Any:
        """Execute a JSON-RPC call with automatic reconnection on transient failures.

        Issue #26: Implements exponential backoff and automatic session recovery
        for connection errors that can occur with OMV 8.2.10-1+. All retry
        attempts run inside a single lock acquisition to prevent self-deadlock.

        Args:
            service: OMV RPC service name.
            method: OMV RPC method name.
            params: Optional parameters to pass to the RPC call.
            max_retries: Maximum number of retry attempts on connection errors.
                Set to 0 to disable retries (e.g. for calls that are known to
                fail permanently on certain devices).
        """
        async with self._lock:
            last_err: Exception | None = None
            for attempt in range(max_retries + 1):
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
                except (OMVConnectionError, aiohttp.ClientError) as err:
                    last_err = err
                    if attempt >= max_retries:
                        break
                    # Exponential backoff: 1s, 2s, 4s between retries
                    wait_seconds = 2**attempt
                    _LOGGER.warning(
                        "Connection error for %s.%s on %s, retrying in %ds (attempt %d/%d): %s",
                        service,
                        method,
                        self._host,
                        wait_seconds,
                        attempt + 1,
                        max_retries,
                        err,
                    )
                    await asyncio.sleep(wait_seconds)
                    # Attempt session recovery before next retry
                    try:
                        await self._async_ensure_session()
                        await self._async_login()
                    except Exception as reconnect_err:
                        _LOGGER.debug(
                            "Failed to reconnect during retry: %s",
                            reconnect_err,
                        )
            log_fn = _LOGGER.debug if max_retries == 0 else _LOGGER.error
            log_fn(
                "Max retries exceeded for %s.%s on %s: %s",
                service,
                method,
                self._host,
                last_err,
            )
        raise OMVConnectionError(f"Failed to reach OMV after {max_retries} retries: {last_err}") from last_err

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

        # ssl=False means "SSL enabled, skip certificate verification" in aiohttp;
        # for plain http:// URLs the parameter is ignored. True is aiohttp's own
        # default (verify certificates), so the non-skip branch just states it
        # explicitly instead of relying on the parameter being omitted.
        ssl_param: bool = not (self._ssl and not self._verify_ssl)
        try:
            async with self._session.post(
                f"{self.base_url}/rpc.php",
                headers=headers,
                json=payload,
                ssl=ssl_param,
            ) as response:
                if response.status in (401, 403):
                    try:
                        error_body = await response.json(content_type=None)
                    except ValueError:
                        error_body = None
                    is_omv_rpc_body = isinstance(error_body, dict) and (
                        "error" in error_body or "response" in error_body
                    )
                    _LOGGER.debug(
                        "OMV RPC HTTP auth failure [%s] %s.%s host=%r status=%s "
                        "has_session_header=%s cookie_names=%s is_omv_rpc_body=%s",
                        self._source,
                        service,
                        method,
                        self._host,
                        response.status,
                        headers is not None,
                        self._cookie_names(),
                        is_omv_rpc_body,
                    )
                    if not is_omv_rpc_body:
                        # A bare 401/403 without an OMV JSON-RPC envelope did not
                        # come from OMV's own rpc.php (e.g. a reverse proxy, WAF,
                        # or fail2ban rejecting the request) — treat it as a
                        # connectivity problem rather than a credentials problem.
                        raise OMVConnectionError(f"Unexpected HTTP {response.status} response (not an OMV RPC body)")
                    raise OMVAuthError(f"OMV returned HTTP {response.status}")
                if response.status >= 500:
                    body = (await response.text())[:500]
                    _LOGGER.debug(
                        "OMV RPC HTTP server failure [%s] %s.%s host=%r status=%s "
                        "has_session_header=%s cookie_names=%s body=%r",
                        self._source,
                        service,
                        method,
                        self._host,
                        response.status,
                        headers is not None,
                        self._cookie_names(),
                        body,
                    )
                    raise OMVConnectionError(f"OMV returned HTTP {response.status}: {body}")
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

    async def async_apply_config(self) -> None:
        """Apply pending OMV configuration changes.

        Calls Config.applyChanges which runs omv-mkconf for all dirty modules,
        persisting the current in-memory configuration to disk.

        The RPC requires ``modules`` (array, empty = all dirty) and ``force``
        (bool). Passing an empty modules list lets OMV resolve the dirty set
        automatically.
        """
        await self.async_call("Config", "applyChanges", {"modules": [], "force": False})

    async def async_close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            # Let aiohttp run deferred transport shutdown callbacks before
            # pytest cleanup checks for lingering background threads.
            await asyncio.sleep(0)
        self._session = None
        self._session_id = None

#!/usr/bin/env python3
"""Run a live RPC compatibility probe against one or more OMV hosts."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import getpass
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

import aiohttp

_SESSION_EXPIRED_CODES = {5001, 5002}
_INVALID_LOGIN_MESSAGES = (
    "incorrect username or password",
    "invalid username or password",
    "authentication failed",
)
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ProbeError(Exception):
    """Base exception for the compatibility probe."""


class ProbeAuthError(ProbeError):
    """Authentication failed or the OMV session expired."""


class ProbeConnectionError(ProbeError):
    """The OMV endpoint is not reachable or returned invalid data."""


@dataclass(slots=True)
class TargetConfig:
    """Runtime target configuration."""

    name: str
    host: str
    port: int
    use_ssl: bool
    verify_ssl: bool

    @property
    def base_url(self) -> str:
        """Return the OMV base URL for this target."""
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"


@dataclass(slots=True)
class EndpointResult:
    """Result metadata for a single RPC call."""

    service: str
    method: str
    optional: bool
    status: str
    elapsed_ms: int
    response_type: str | None = None
    record_count: int | None = None
    sample_keys: list[str] | None = None
    note: str | None = None
    error: str | None = None


@dataclass(slots=True)
class TargetReport:
    """Aggregated probe report for one OMV target."""

    name: str
    host: str
    port: int
    base_url: str
    version: str | None
    major_version: int
    endpoints: list[EndpointResult]


class OMVProbeClient:
    """Minimal OMV JSON-RPC client for live compatibility probes."""

    def __init__(self, target: TargetConfig, username: str, password: str) -> None:
        self._target = target
        self._username = username
        self._password = password
        self._session_id: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def async_connect(self) -> dict[str, Any]:
        """Create a session, authenticate and fetch system information."""
        await self._async_ensure_session()
        await self._async_login()
        response = await self.async_call("System", "getInformation")
        return response if isinstance(response, dict) else {}

    async def async_close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._session_id = None

    async def async_call(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an RPC call and retry once on session expiry."""
        async with self._lock:
            try:
                return await self._async_raw_call(service, method, params, options=options)
            except ProbeAuthError:
                await self._async_login()
                return await self._async_raw_call(service, method, params, options=options)

    async def _async_ensure_session(self) -> None:
        """Create or recreate the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

        connector = aiohttp.TCPConnector(
            ssl=self._target.verify_ssl if self._target.use_ssl else False
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=_REQUEST_TIMEOUT,
        )

    async def _async_login(self) -> None:
        """Authenticate against OMV and cache the session identifier."""
        self._session_id = None
        response = await self._async_raw_call(
            "session",
            "login",
            {"username": self._username, "password": self._password},
            options=None,
        )
        if not isinstance(response, dict) or not response.get("authenticated"):
            raise ProbeAuthError("Invalid credentials")

        session_id = response.get("sessionid")
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id

    async def _async_raw_call(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a raw JSON-RPC POST request."""
        if not self._session or self._session.closed:
            raise ProbeConnectionError("No active OMV session")

        headers = None
        if self._session_id:
            headers = {"X-OPENMEDIAVAULT-SESSIONID": self._session_id}

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
                f"{self._target.base_url}/rpc.php",
                json=payload,
                headers=headers,
            ) as response:
                if response.status in (401, 403):
                    raise ProbeAuthError(f"OMV returned HTTP {response.status}")
                if response.status >= 500:
                    raise ProbeConnectionError(f"OMV returned HTTP {response.status}")
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ProbeConnectionError(
                f"Connection to {self._target.host} failed: {err}"
            ) from err
        except ValueError as err:
            raise ProbeConnectionError(
                f"Invalid JSON response from {self._target.host}: {err}"
            ) from err

        error = data.get("error")
        if error:
            code = error.get("code", 0)
            message = str(error.get("message", "Unknown error"))
            if (
                service == "session"
                and method == "login"
                and _is_invalid_login_message(message)
            ):
                raise ProbeAuthError(message)
            if code in _SESSION_EXPIRED_CODES:
                raise ProbeAuthError(message)
            raise ProbeError(f"RPC {service}.{method} failed: {message}")

        return data.get("response")


def _is_invalid_login_message(message: str) -> bool:
    """Return whether OMV reported invalid credentials."""
    lowered = message.casefold()
    return any(fragment in lowered for fragment in _INVALID_LOGIN_MESSAGES)


def parse_target(value: str, *, port: int, use_ssl: bool, verify_ssl: bool) -> TargetConfig:
    """Parse a target specification in the form name=host."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "Target must be specified as name=host, for example omv7=192.168.178.41"
        )

    name, host = value.split("=", 1)
    name = name.strip()
    host = host.strip()
    if not name or not host:
        raise argparse.ArgumentTypeError(
            "Target must include both a non-empty name and host"
        )

    return TargetConfig(
        name=name,
        host=host,
        port=port,
        use_ssl=use_ssl,
        verify_ssl=verify_ssl,
    )


def _major_version(version: str | None) -> int:
    """Extract the OMV major version from the version string."""
    if not version:
        return 0
    match = re.match(r"(\d+)", version)
    return int(match.group(1)) if match else 0


def _records_from_response(response: Any) -> list[dict[str, Any]]:
    """Normalize list-like RPC responses into a flat record list."""
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(response.get("records"), list):
            return [
                item for item in response["records"] if isinstance(item, dict)
            ]
    return []


def _response_contains_records(response: Any) -> bool:
    """Return whether the RPC response contains tabular records."""
    return bool(_records_from_response(response))


def _sample_keys_from_response(response: Any) -> list[str] | None:
    """Return representative keys from a response payload."""
    records = _records_from_response(response)
    if records:
        return sorted(str(key) for key in records[0].keys())[:8]
    if isinstance(response, dict):
        return sorted(str(key) for key in response.keys())[:8]
    return None


def _summarize_response(response: Any) -> tuple[str, int | None, list[str] | None]:
    """Summarize response type, record count and key sample."""
    response_type = type(response).__name__
    records = _records_from_response(response)
    if records:
        return response_type, len(records), _sample_keys_from_response(response)
    if isinstance(response, dict):
        return response_type, None, _sample_keys_from_response(response)
    if isinstance(response, list):
        return response_type, len(response), _sample_keys_from_response(response)
    return response_type, None, None


async def _call_endpoint(
    client: OMVProbeClient,
    *,
    service: str,
    method: str,
    optional: bool,
    params: dict[str, Any] | None = None,
) -> tuple[EndpointResult, Any | None]:
    """Call one endpoint and return structured result metadata."""
    started = time.perf_counter()
    try:
        response = await client.async_call(service, method, params)
    except (ProbeAuthError, ProbeConnectionError, ProbeError) as err:
        status = "optional-error" if optional else "error"
        result = EndpointResult(
            service=service,
            method=method,
            optional=optional,
            status=status,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(err),
        )
        return result, None

    response_type, record_count, sample_keys = _summarize_response(response)
    result = EndpointResult(
        service=service,
        method=method,
        optional=optional,
        status="ok",
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        response_type=response_type,
        record_count=record_count,
        sample_keys=sample_keys,
    )
    return result, response


def _first_smart_disk(disks_response: Any) -> str | None:
    """Pick one disk device file for a representative SMART attributes call."""
    for disk in _records_from_response(disks_response):
        devicename = str(disk.get("devicename") or "")
        canonical = str(disk.get("canonicaldevicefile") or "")
        if not canonical:
            continue
        if devicename.startswith(("mmcblk", "sr", "bcache")):
            continue
        return canonical
    return None


async def probe_target(
    target: TargetConfig,
    *,
    username: str,
    password: str,
) -> TargetReport:
    """Run the full compatibility probe for a single target."""
    client = OMVProbeClient(target, username, password)
    endpoint_results: list[EndpointResult] = []
    version = None
    major_version = 0

    try:
        system_info = await client.async_connect()
        version = str(system_info.get("version") or "unknown")
        major_version = _major_version(version)

        system_response_type, _, system_keys = _summarize_response(system_info)
        endpoint_results.append(
            EndpointResult(
                service="System",
                method="getInformation",
                optional=False,
                status="ok",
                elapsed_ms=0,
                response_type=system_response_type,
                sample_keys=system_keys,
                note="Fetched during session bootstrap",
            )
        )

        cpu_result, _ = await _call_endpoint(
            client,
            service="CpuTemp",
            method="get",
            optional=True,
        )
        endpoint_results.append(cpu_result)

        filesystem_result, _ = await _call_endpoint(
            client,
            service="FileSystemMgmt",
            method="enumerateFilesystems",
            optional=False,
        )
        endpoint_results.append(filesystem_result)

        services_result, _ = await _call_endpoint(
            client,
            service="Services",
            method="getStatus",
            optional=False,
        )
        endpoint_results.append(services_result)

        network_result, _ = await _call_endpoint(
            client,
            service="Network",
            method="enumerateDevices",
            optional=False,
        )
        endpoint_results.append(network_result)

        disks_result, disks_response = await _call_endpoint(
            client,
            service="DiskMgmt",
            method="enumerateDevices",
            optional=False,
        )
        endpoint_results.append(disks_result)

        smart_method = "getListBg" if major_version >= 7 else "getList"
        smart_params = {"start": 0, "limit": 100} if smart_method == "getListBg" else None
        smart_result, smart_response = await _call_endpoint(
            client,
            service="Smart",
            method=smart_method,
            optional=False,
            params=smart_params,
        )
        if smart_method == "getListBg" and smart_result.status == "ok":
            if not _response_contains_records(smart_response):
                smart_result.status = "fallback-required"
                smart_result.note = "Returned a background task handle or empty payload"
                fallback_result, _ = await _call_endpoint(
                    client,
                    service="Smart",
                    method="getList",
                    optional=False,
                    params=smart_params,
                )
                endpoint_results.append(smart_result)
                endpoint_results.append(fallback_result)
            else:
                endpoint_results.append(smart_result)
        else:
            endpoint_results.append(smart_result)

        smart_device = _first_smart_disk(disks_response)
        if smart_device:
            attributes_result, _ = await _call_endpoint(
                client,
                service="Smart",
                method="getAttributes",
                optional=True,
                params={"devicefile": smart_device},
            )
            endpoint_results.append(attributes_result)
        else:
            endpoint_results.append(
                EndpointResult(
                    service="Smart",
                    method="getAttributes",
                    optional=True,
                    status="skipped",
                    elapsed_ms=0,
                    note="No eligible disk with canonicaldevicefile available",
                )
            )

        compose_result, _ = await _call_endpoint(
            client,
            service="compose",
            method="getContainerList",
            optional=True,
        )
        endpoint_results.append(compose_result)

        kvm_result, _ = await _call_endpoint(
            client,
            service="Kvm",
            method="getVmList",
            optional=True,
            params={"start": 0, "limit": 999},
        )
        endpoint_results.append(kvm_result)

        zfs_result, _ = await _call_endpoint(
            client,
            service="zfs",
            method="listPools",
            optional=True,
        )
        endpoint_results.append(zfs_result)
    except (ProbeAuthError, ProbeConnectionError, ProbeError) as err:
        endpoint_results.append(
            EndpointResult(
                service="session",
                method="connect",
                optional=False,
                status="error",
                elapsed_ms=0,
                error=str(err),
            )
        )
    finally:
        await client.async_close()

    return TargetReport(
        name=target.name,
        host=target.host,
        port=target.port,
        base_url=target.base_url,
        version=version,
        major_version=major_version,
        endpoints=endpoint_results,
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Probe OMV RPC compatibility against live targets"
    )
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        help="Target in the form name=host. Repeat for multiple OMV systems.",
    )
    parser.add_argument("--username", required=True, help="OMV username")
    parser.add_argument(
        "--password",
        help="OMV password. Prefer --password-env or the interactive prompt.",
    )
    parser.add_argument(
        "--password-env",
        default="OMV_PASSWORD",
        help="Environment variable to read the password from before prompting.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=80,
        help="RPC port used for every target. Defaults to 80.",
    )
    parser.add_argument(
        "--ssl",
        action="store_true",
        help="Use HTTPS instead of HTTP.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification when --ssl is set.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path.",
    )
    return parser


def _resolve_password(args: argparse.Namespace) -> str:
    """Resolve the password from CLI, environment or interactive prompt."""
    if args.password:
        return args.password

    if args.password_env:
        env_password = os.getenv(args.password_env)
        if env_password:
            return env_password

    return getpass.getpass("OMV password: ")


def _serialize_reports(reports: list[TargetReport]) -> dict[str, Any]:
    """Convert dataclass reports into JSON-serializable dictionaries."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "targets": [asdict(report) for report in reports],
    }


def _print_summary(reports: list[TargetReport]) -> None:
    """Render a concise terminal summary."""
    for report in reports:
        version = report.version or "unknown"
        print(
            f"\n[{report.name}] {report.base_url} | OMV {version} | major={report.major_version}"
        )
        for endpoint in report.endpoints:
            prefix = f"  {endpoint.service}.{endpoint.method}: {endpoint.status}"
            details: list[str] = []
            if endpoint.response_type:
                details.append(f"type={endpoint.response_type}")
            if endpoint.record_count is not None:
                details.append(f"records={endpoint.record_count}")
            if endpoint.sample_keys:
                details.append(f"keys={','.join(endpoint.sample_keys)}")
            if endpoint.note:
                details.append(endpoint.note)
            if endpoint.error:
                details.append(endpoint.error)
            if details:
                print(f"{prefix} | {'; '.join(details)}")
            else:
                print(prefix)


async def _async_main(args: argparse.Namespace) -> int:
    """Run the probe and optionally persist the JSON report."""
    password = _resolve_password(args)
    targets = [
        parse_target(
            value,
            port=args.port,
            use_ssl=args.ssl,
            verify_ssl=not args.insecure,
        )
        for value in args.target
    ]

    reports = [
        await probe_target(target, username=args.username, password=password)
        for target in targets
    ]
    _print_summary(reports)

    if args.output:
        payload = _serialize_reports(reports)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"\nJSON report written to {args.output}")

    return 0 if all(
        endpoint.status not in {"error"}
        for report in reports
        for endpoint in report.endpoints
        if not endpoint.optional
    ) else 1


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
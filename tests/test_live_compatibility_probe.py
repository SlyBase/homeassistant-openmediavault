"""Tests for the live OMV RPC compatibility probe helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_probe_module():
    """Load the compatibility probe script as a module."""
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "check_omv_rpc_compatibility.py"
    spec = importlib.util.spec_from_file_location("omv_live_probe", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_target_returns_runtime_config() -> None:
    """Parsing a target should preserve the passed transport settings."""
    probe = _load_probe_module()

    target = probe.parse_target(
        "omv7=192.168.178.41",
        port=443,
        use_ssl=True,
        verify_ssl=False,
    )

    assert target.name == "omv7"
    assert target.host == "192.168.178.41"
    assert target.base_url == "https://192.168.178.41:443"
    assert target.verify_ssl is False


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ([{"name": "disk"}], True),
        ({"data": [{"name": "disk"}]}, True),
        ({"records": [{"name": "disk"}]}, True),
        ("task-123", False),
        ({"data": []}, False),
    ],
)
def test_response_contains_records(response, expected: bool) -> None:
    """Record detection should match the coordinator-compatible response shapes."""
    probe = _load_probe_module()

    assert probe._response_contains_records(response) is expected


def test_summarize_response_prefers_record_shape() -> None:
    """Response summaries should expose type, record count and sample keys."""
    probe = _load_probe_module()

    response_type, record_count, sample_keys = probe._summarize_response(
        {"data": [{"devicename": "sda", "temperature": 32, "overallstatus": "PASSED"}]}
    )

    assert response_type == "dict"
    assert record_count == 1
    assert sample_keys == ["devicename", "overallstatus", "temperature"]


def test_summarize_response_flattens_nested_compose_fields() -> None:
    """Nested compose metadata should show up in the sample key summary."""
    probe = _load_probe_module()

    response_type, record_count, sample_keys = probe._summarize_response(
        {
            "data": [
                {
                    "id": "ctr-vaultwarden",
                    "name": "vaultwarden",
                    "labels": {"org.opencontainers.image.version": "1.33.2"},
                    "annotations": {"org.opencontainers.image.version": "1.33.2"},
                    "mounts": [
                        {
                            "Type": "volume",
                            "Name": "vaultwarden_data",
                            "Destination": "/data",
                        }
                    ],
                }
            ]
        }
    )

    assert response_type == "dict"
    assert record_count == 1
    assert "labels.org.opencontainers.image.version" in sample_keys
    assert "annotations.org.opencontainers.image.version" in sample_keys
    assert "mounts[].Type" in sample_keys
    assert "mounts[].Name" in sample_keys


@pytest.mark.asyncio
async def test_probe_target_requests_compose_file_list(monkeypatch) -> None:
    """The live probe should cover both compose list endpoints."""
    probe = _load_probe_module()
    calls: list[tuple[str, str, dict[str, int] | None]] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = False

        async def async_connect(self):
            return {"version": "8.1.2-1"}

        async def async_close(self) -> None:
            self.closed = True

    async def fake_call_endpoint(client, *, service, method, optional, params=None):
        calls.append((service, method, params))
        if (service, method) == ("DiskMgmt", "enumerateDevices"):
            return (
                probe.EndpointResult(
                    service=service,
                    method=method,
                    optional=optional,
                    status="ok",
                    elapsed_ms=1,
                ),
                [{"devicename": "sda", "canonicaldevicefile": "/dev/sda"}],
            )
        if (service, method) == ("Smart", "getListBg"):
            return (
                probe.EndpointResult(
                    service=service,
                    method=method,
                    optional=optional,
                    status="ok",
                    elapsed_ms=1,
                ),
                {"data": []},
            )
        return (
            probe.EndpointResult(
                service=service,
                method=method,
                optional=optional,
                status="ok",
                elapsed_ms=1,
            ),
            {"data": []},
        )

    monkeypatch.setattr(probe, "OMVProbeClient", FakeClient)
    monkeypatch.setattr(probe, "_call_endpoint", fake_call_endpoint)

    target = probe.parse_target(
        "omv8=192.168.178.40",
        port=80,
        use_ssl=False,
        verify_ssl=True,
    )
    report = await probe.probe_target(target, username="admin", password="secret")

    assert report.major_version == 8
    assert (
        "compose",
        "getContainerList",
        {"start": 0, "limit": 999},
    ) in calls
    assert (
        "compose",
        "getFileList",
        {"start": 0, "limit": 999},
    ) in calls

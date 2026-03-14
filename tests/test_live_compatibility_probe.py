"""Tests for the live OMV RPC compatibility probe helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _load_probe_module():
    """Load the compatibility probe script as a module."""
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check_omv_rpc_compatibility.py"
    )
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
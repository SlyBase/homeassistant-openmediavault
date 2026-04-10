"""Consistency checks for manifest.json and hacs.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "custom_components" / "omv" / "manifest.json"
HACS_JSON = ROOT / "hacs.json"

_HA_VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d+$")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def hacs() -> dict:
    return json.loads(HACS_JSON.read_text())


# ---------------------------------------------------------------------------
# manifest.json structure
# ---------------------------------------------------------------------------


def test_manifest_required_fields_present(manifest: dict) -> None:
    """All fields required by hassfest must be present."""
    required = {"domain", "name", "version", "homeassistant", "codeowners", "documentation", "issue_tracker"}
    missing = required - manifest.keys()
    assert not missing, f"manifest.json is missing required fields: {missing}"


def test_manifest_homeassistant_version_format(manifest: dict) -> None:
    """homeassistant field must follow YYYY.M.patch format."""
    assert _HA_VERSION_RE.match(manifest["homeassistant"]), (
        f"manifest.json homeassistant value '{manifest['homeassistant']}' does not match expected format YYYY.M.patch"
    )


def test_manifest_version_format(manifest: dict) -> None:
    """Integration version must follow semantic versioning."""
    version_re = re.compile(r"^\d+\.\d+\.\d+$")
    assert version_re.match(manifest["version"]), (
        f"manifest.json version '{manifest['version']}' is not in X.Y.Z format"
    )


def test_manifest_domain_is_omv(manifest: dict) -> None:
    """Domain must always be 'omv'."""
    assert manifest["domain"] == "omv"


# ---------------------------------------------------------------------------
# hacs.json structure
# ---------------------------------------------------------------------------


def test_hacs_homeassistant_version_format(hacs: dict) -> None:
    """hacs.json homeassistant field must follow YYYY.M.patch format."""
    assert _HA_VERSION_RE.match(hacs["homeassistant"]), (
        f"hacs.json homeassistant value '{hacs['homeassistant']}' does not match expected format YYYY.M.patch"
    )


# ---------------------------------------------------------------------------
# Cross-file consistency
# ---------------------------------------------------------------------------


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def test_hacs_homeassistant_matches_manifest(manifest: dict, hacs: dict) -> None:
    """hacs.json and manifest.json must require the same minimum HA version."""
    assert hacs["homeassistant"] == manifest["homeassistant"], (
        f"Version mismatch: hacs.json requires HA {hacs['homeassistant']} "
        f"but manifest.json requires HA {manifest['homeassistant']}. "
        "Update both files to the same value."
    )


def test_manifest_homeassistant_not_older_than_hacs(manifest: dict, hacs: dict) -> None:
    """manifest.json minimum HA version must be >= hacs.json minimum version."""
    assert _version_tuple(manifest["homeassistant"]) >= _version_tuple(hacs["homeassistant"]), (
        f"manifest.json homeassistant ({manifest['homeassistant']}) is older than "
        f"hacs.json homeassistant ({hacs['homeassistant']}). "
        "The integration would be shown as compatible with versions it no longer supports."
    )

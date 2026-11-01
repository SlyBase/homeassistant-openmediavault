"""Tests for platform PARALLEL_UPDATES declarations (quality scale: parallel-updates)."""

from __future__ import annotations

from typing import Any

import custom_components.omv as omv_pkg
from custom_components.omv.const import PLATFORMS


def test_every_platform_declares_parallel_updates() -> None:
    """Every entity platform module defines a module-level PARALLEL_UPDATES."""
    missing: list[str] = []
    for platform in PLATFORMS:
        module = __import__(f"custom_components.omv.{platform.value}", fromlist=["PARALLEL_UPDATES"])
        if not hasattr(module, "PARALLEL_UPDATES"):
            missing.append(platform.value)
    assert not missing, f"platforms missing PARALLEL_UPDATES: {missing}"


def test_parallel_updates_values_are_non_negative_integers() -> None:
    """PARALLEL_UPDATES is a non-negative int in every platform module."""
    for platform in PLATFORMS:
        module = __import__(f"custom_components.omv.{platform.value}", fromlist=["PARALLEL_UPDATES"])
        value: Any = module.PARALLEL_UPDATES
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{platform.value}: PARALLEL_UPDATES must be an int, got {value!r}"
        )
        assert value >= 0, f"{platform.value}: PARALLEL_UPDATES must be >= 0"


def test_read_only_platforms_use_unlimited_parallelism() -> None:
    """Coordinator-fed read-only platforms (sensor, binary_sensor) use 0."""
    for platform in ("sensor", "binary_sensor"):
        module = __import__(f"custom_components.omv.{platform}", fromlist=["PARALLEL_UPDATES"])
        assert module.PARALLEL_UPDATES == 0, f"{platform} must be 0"


def test_action_platforms_are_throttled_to_one() -> None:
    """Platforms issuing device commands (button, switch, update) limit to 1."""
    for platform in ("button", "switch", "update"):
        module = __import__(f"custom_components.omv.{platform}", fromlist=["PARALLEL_UPDATES"])
        assert module.PARALLEL_UPDATES == 1, f"{platform} must be 1"


def test_omv_package_is_importable() -> None:
    """Sanity: the package import used above resolves."""
    assert omv_pkg.__name__ == "custom_components.omv"

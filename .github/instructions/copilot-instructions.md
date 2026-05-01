---
applyTo: '**'
description: 'Project overview, architecture, build commands, and coding conventions for the homeassistant-openmediavault integration'
---

# GitHub Copilot Instructions — homeassistant-openmediavault

Home Assistant custom integration that monitors and controls OpenMediaVault (OMV) NAS devices via the OMV JSON-RPC API.

## Build & Test Commands

```bash
# Install all dev/test dependencies
pip install -e ".[test,dev]"

# Run tests (always use the venv Python)
.venv/bin/python -m pytest tests -q

# Run tests with coverage
.venv/bin/python -m pytest tests --cov=custom_components/omv --cov-report=term-missing

# Lint (ruff)
.venv/bin/python -m ruff check custom_components tests

# Type-check
.venv/bin/python -m mypy custom_components/omv
```

> **Important:** Always use `.venv/bin/python` — the system Python may not have the required packages installed.

## Architecture Overview

```
omv_api.py          Async JSON-RPC client (aiohttp) → OMV /rpc.php
    ↓
coordinator.py      DataUpdateCoordinator — fetches, normalises, filters all OMV data (60s default)
    ↓
sensor_types.py     OMVSensorDescription descriptor objects
binary_sensor_types.py  OMVBinarySensorDescription descriptors
    ↓
sensor.py / binary_sensor.py / button.py   HA platform entry points
    ↓
entity.py           Shared base entity (links entity descriptions to coordinator data)
```

- `config_flow.py` — initial setup (host/credentials) + options flow (scan interval, resource filtering, feature flags)
- `const.py` — all constants: `DOMAIN="omv"`, default ports, `CONF_SELECTED_*` filter keys
- `diagnostics.py` — HA diagnostics support
- `exceptions.py` — `OMVAuthError`, `OMVConnectionError`, `OMVApiError`

## Key Conventions

### Entity Descriptor Pattern
New entities are defined as `OMVSensorDescription` / `OMVBinarySensorDescription` dataclasses:
```python
@dataclass(frozen=True, kw_only=True)
class OMVSensorDescription(SensorEntityDescription):
    data_path: str           # Key in coordinator.data (e.g. "disk", "fs")
    value_fn: Callable[...]  # Extracts the sensor value from the item/dict
    extra_attrs_fn: Callable[...] | None = None
    is_collection: bool = False   # True = one entity per item (disk, container, …)
    collection_key: str | None = None  # Unique ID field (e.g. "disk_key", "uuid")
    name_key: str | None = None        # Display name field
```
Never hard-code English `_attr_name`. Use `translation_key` + `_attr_translation_placeholders` for dynamic names.

### Coordinator Data Keys
| Key | Content |
|-----|---------|
| `hwinfo` | CPU util, memory, temperature, uptime, update availability |
| `disk` | Physical disks + synthesised RAID/md* records |
| `fs` | Filesystems mapped to disks (uuid, mount, size, used) |
| `service` | OMV service status records |
| `network` | Network interfaces with precomputed TX/RX Mbps rates |
| `smart` | SMART records (raw data) |
| `compose` | Docker containers |
| `compose_projects` | Compose projects with container counts |
| `compose_volumes` | Container volumes (size only when present in payload) |
| `compose_summary` | Aggregated container counts |
| `kvm` | KVM virtual machines |
| `zfs` | ZFS pool status |
| `raid` | RAID arrays |

### API Client Gotchas
- **`CookieJar(unsafe=True)`** is required for IP-based hosts — without it the session cookie is silently dropped and subsequent RPCs fail with a spurious auth error.
- `OMVAPI.async_call()` auto-re-authenticates on session expiry (error codes 5001/5002).
- All calls go through an async lock — no concurrent requests to the same session.
- OMV size fields can arrive as bare numeric strings (no unit) — treat them as **bytes**, not GB.

### SMART on OMV 8
OMV 8 uses `Smart.getListBg` which returns a background-task handle instead of records. The coordinator falls back to `Smart.getList` with `{start: 0, limit: 100}` when `getListBg` yields no records. Both methods need explicit `start` — omitting it causes `"Missing 'required' attribute 'start'"`.

### Resource Filtering
Options flow stores `CONF_SELECTED_*` lists. `coordinator.filter_data_by_selection()` prunes `coordinator.data` before entities read it. When a resource is de-selected it disappears from the filtered data but its entity/disk registry entries must be removed explicitly after a reload.

### ZFS on OMV 8
`zfs.listPools` returns IDs like `root/pool-<Name>`. `enumerateFilesystems` reports ZFS filesystems via `mountpoint` (not `mountdir`) and without a `/dev` parent. Map by mountpoint and use size correlation as a fallback for device association.

### Network Rate Calculation
TX/RX rates become available only after the **second** coordinator update (deltas over the interval in seconds, result in Mbps).

## Testing Patterns

Tests live in `tests/` and use `pytest-homeassistant-custom-component` + `aioresponses`:

```python
# conftest.py provides:
config_entry   # MockConfigEntry with standard credentials
sample_data    # Complete normalized coordinator.data dict with realistic values
```

- `asyncio_mode = "auto"` in `pyproject.toml` — no need to mark individual tests with `@pytest.mark.asyncio`.
- Mock HTTP responses with `aioresponses`.
- `enable_custom_integrations` fixture is applied automatically.
- Live-target tests for OMV7/OMV8 are in `test_live_compatibility_probe.py` — these require network access and are not part of the normal CI run.

## OMV Version Compatibility

| Feature | OMV 7 | OMV 8 |
|---------|-------|-------|
| SMART method | `Smart.getList` (direct) | `Smart.getListBg` + fallback |
| ZFS pool IDs | standard names | `root/pool-<Name>` |
| Compose RPC | available | may be absent |
| Filesystem key | `mountdir` | may use `mountpoint` |

Supported: OMV 7 and 8. Live targets (as of 2026-03-14): OMV7 on `192.168.178.41` (7.7.24-7), OMV8 on `192.168.178.40` (8.1.2-1).

## Integration Domain & Platforms

- Domain: `omv`
- Platforms: `sensor`, `binary_sensor`, `button`
- Minimum HA: 2025.5
- Python: ≥ 3.13.2

## Lint / Style

- Ruff with rules `E, F, I, W, UP, B, SIM, RUF`, target Python 3.13, line-length 88.
- `check_untyped_defs = true`, `disallow_untyped_defs = true` in mypy — all new code needs type annotations.

## Documentation Policy

Applies whenever a `.py`, `.sh`, `.yaml`, or `.yml` file is modified.

### CHANGELOG.md

Always add an entry under `## [Unreleased]` (create it below `# Changelog` if absent). Use the appropriate subsection:

- `### Added` – new functions, classes, scripts, or features
- `### Changed` – modified behaviour, renamed parameters, updated logic
- `### Fixed` – bug fixes
- `### Removed` – deleted functions, parameters, or files
- `### Deprecated` – features to be removed in a future version

One line per change is sufficient. All entries must be written in **English**.

### Docstrings

When changing a Python function or class, update its docstring to reflect the new behaviour, parameters, and return values. Use Google style (`Args:`, `Returns:`, `Raises:`). Never change a public function signature without updating its docstring.

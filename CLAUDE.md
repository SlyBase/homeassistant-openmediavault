# homeassistant-openmediavault

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

Always use `.venv/bin/python` — the system Python may not have the required packages installed.

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

- `config_flow.py` — initial setup (host/credentials, with a 2FA/TOTP second step when required) + reconfigure flow (update host/port/SSL/credentials on an existing entry in place, e.g. to switch to HTTPS or enable 2FA, without deleting it) + options flow (scan interval, resource filtering, feature flags)
- `const.py` — all constants: `DOMAIN="omv"`, default ports, `CONF_SELECTED_*` filter keys
- `diagnostics.py` — HA diagnostics support
- `exceptions.py` — `OMVAuthError` (+ `OMVTwoFactorRequiredError` subclass for OMV 8.5+ 2FA challenges), `OMVConnectionError`, `OMVApiError`

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
| `network` | Network interfaces with precomputed TX/RX Mbps rates, `wol` flag and lowercased `mac` (from raw `ether`); WoL buttons send the magic packet from the HA host via the stdlib-only `wol.py` (OMV API is down in standby) |
| `smart` | SMART records (raw data) |
| `compose` | Docker containers |
| `compose_projects` | Compose projects with container counts |
| `compose_volumes` | Container volumes (size only when present in payload) |
| `compose_summary` | Aggregated container counts |
| `kvm` | KVM virtual machines |
| `zfs` | ZFS pool status, enriched with `scrubactive`/`scrubstate`/`lastscrub` (pass-through from `listPools`) and computed `dataset_count`/`snapshot_count` |
| `zfs_datasets` | ZFS datasets from `zfs.listDatasets` (`dataset_key`= plain full path like `tank/media`, NEVER the OMV8 tree id; `pool` = first path segment; `used_gb`/`available_gb`; only `Filesystem`/`Volume` types). Fetched only when pools exist; filtered by the pool selection. Snapshots are aggregated per pool via `zfs.getAllSnapshots` — no per-snapshot entities |
| `raid` | RAID arrays |
| `tempmon` | Temperature sensors from `openmediavault-tempmon` plugin (empty list when plugin absent) |
| `nut` | UPS metrics parsed from the `Nut.getStats` plain-string `upsc` output (`battery_charge`, `battery_runtime`, `load`, `status`, `on_battery`, `model`, `raw`; empty dict when plugin absent or NUT service disabled) |
| `rsync` | Rsync jobs from `Rsync.getList` (`rsync_key`=uuid, `name` from comment with "src → dest" fallback, `enabled`, `type`, `mode`, `srcname`, `destname`, `schedule`; always passed through unfiltered) |
| `cron` | User-defined cron jobs from `Cron.getList` with required `type: ["userdefined"]` (`cron_key`=uuid, `name` from comment with shortened-command fallback, `enabled`, `command`, `username`, `schedule`; always passed through unfiltered — the `selected_cron_jobs` option is an opt-in for button creation read directly from `entry.options` in button.py, NOT a data filter) |

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
| `Kvm.getVmList` | not probed | `RPC service 'Kvm' not found` when `openmediavault-kvm` plugin is not installed — already handled gracefully by `_fetch_optional` (returns `[]`) |

Supported: OMV 7 and 8. Live targets (as of 2026-06-11): OMV7 on `192.168.178.41` (unreachable from dev machine during last probe — not re-verified since 7.7.24-7), OMV8 on `192.168.178.40` (8.3.0-1 "Synchrony"). `compose.getContainerList` confirmed fields: `command, created, execurl, id, image, mounts, name, network, ports, running, state, status`. `Kvm` RPC service is absent on this OMV8 instance (plugin not installed) — `Kvm.getVmList` payload shape for `_normalize_kvm()` (Tier 1, Step 6) could not be verified live; implement defensively with `.get()` defaults so it degrades to an empty list when the plugin/RPC is unavailable.

### Tier 2 RPC facts (source-verified against upstream GitHub, 2026-06-12)

The Tier 2 live probe run (2026-06-12) was **skipped** — `OMV_PASSWORD` was not available in the environment. The probe script covers the endpoints below (`scripts/check_omv_rpc_compatibility.py`); re-run it when credentials are at hand. Until then these facts come from upstream plugin/core sources:

- **`Kvm.doCommand`** (openmediavault-kvm): params `command` (`poweron`/`poweroff`/`force`/`reboot`/`reset`/`pause`/`resume`/`autostartenable`/`autostartdisable`), `name` (VM name), `virttype` (`"vm"`/`"lxc"`), plus string fields `vncport`/`spiceport`/`hostport`/`hostport2` (`"n/a"` is safe). Real `Kvm.getVmList` records carry `vmname` (no `uuid`/`name`), `virttype`, `mem` (bytes), `cpu`, `state` (virsh, e.g. `shut off`), `autostart`, `vncport`/`spiceport`.
- **`Nut.getStats`** (openmediavault-nut): no params; returns a **plain string** — either localized `"Service disabled"` or raw `upsc` output (`key: value` lines such as `battery.charge`, `battery.runtime`, `ups.status: OL`/`OB DISCHRG`, `ups.load`).
- **`Rsync.getList`** (core): `{"start": 0, "limit": -1}`; records `uuid`, `enable`, `comment`, `type`, `mode`, `srcname`, `destname`, cron fields. **`Rsync.execute`** `{"uuid": ...}` returns an execBgProc background filename — fire-and-forget, do not poll.
- **`Cron.getList`** (core): requires `type` as array — `{"start": 0, "limit": -1, "type": ["userdefined"]}`. **`Cron.execute`** `{"uuid": ...}` returns a background filename — fire-and-forget.
- **`zfs.scrubPool`** (openmediavault-zfs): `{"name": "<plain pool name>"}` — plain name, NOT the OMV8 tree id `root/pool-<Name>`. **`zfs.listDatasets`** does no param validation but indexes `start`/`limit`/`sortfield`/`sortdir` directly — always pass all four (`{"start": 0, "limit": -1, "sortfield": None, "sortdir": None}`). **`zfs.getAllSnapshots`** follows `rpc.common.getlist`.
- **`System.standby`** (core): exists alongside `reboot`/`shutdown`; callable without params like `System.reboot`.
- **WoL/MAC**: raw `Network.enumerateDevices` records contain `ether` (MAC) and `wol` on both OMV7 and OMV8 (see `reports/omv-rpc-compatibility.json`).
- **`Session.verify`** (core, OMV 8.5+ 2-step login, source-verified 2026-07-03 against `rpc/session.inc` and the `openmediavault-2fa-totp` plugin's `tfatotp.inc`): despite the `rpc.session.verify` datamodel nominally declaring a top-level `challengeresponse` property, the actual client params must be the raw challenge answer directly — `{"code": "123456"}` for TOTP, NOT `{"challengeresponse": {"code": ...}}`. `session.inc` forwards the client's whole params object as the `challengeresponse` field when it calls the plugin's `verifyChallenge` RPC internally, so a client-side `challengeresponse` wrapper would double-nest. Success response shape matches `Session.login`: `{"status": "authenticated", "sessionid": ..., username, permissions}`. Failure (wrong code, or the 5-minute-TTL pending login already expired) raises an OMV `HttpErrorException(401, ...)`, which surfaces through this integration's `_async_raw_call` as `OMVAuthError` like any other HTTP 401. The pending login is bound to the PHP session cookie set during `Session.login`, so `Session.verify` must reuse the same `aiohttp.ClientSession`/cookie jar — see `OMVAPI.async_submit_two_factor_code`. **Known upstream doc bug (re-verified 2026-07-04):** `AUTH_PLUGIN_GUIDE.md`'s own `Session::verify` spec and curl example show a *wrapped* request (`{"challengeresponse": {"code": "123456"}}`), which contradicts the actual shipped code above — tracing that wrapped shape through `session.inc`'s re-wrap plus `tfatotp.inc`'s `$params['challengeresponse']['code']` read produces a double-nested lookup that would never find the code. Trust the shipped `session.inc`/`tfatotp.inc` behavior (unwrapped `{"code": ...}`, as implemented), not the guide's example.

## Integration Domain & Platforms

- Domain: `omv`
- Platforms: `sensor`, `binary_sensor`, `button`, `switch`, `update`
- Custom services (registered domain-globally in `services.py` from `async_setup_entry`, idempotent, never deregistered on unload): `container_command`, `compose_command`, `apply_config`, `run_rsync_job`. Optional `config_entry_id` auto-resolves when exactly one entry is loaded; container/project/job inputs are matched against coordinator data by key/name/id — unknown projects/jobs raise `ServiceValidationError`, unknown containers pass through verbatim.
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

## Completion Workflow

Before claiming a task complete:

1. **Local validation** — run `pytest tests/ -q` (or a focused subset). No failures allowed.
2. **Commit** — one conventional commit per completed task (do not push; CI handles that).
3. **Deploy** — run the VS Code task `HASS: Deploy to HomeAssistant (SSH)` (or `.github/scripts/deploy-to-homeassistant.sh`) and wait for Home Assistant to come back online.
4. **Smoke test** — use the Home Assistant MCP tools to confirm at least one OMV entity returns a non-`unavailable` state (e.g. `mcp_homeassistant_ha_search_entities` → `mcp_homeassistant_ha_get_state`).
5. **Failure handling** — if any step fails, report the failed step, error signal, and the next repair step. Do not claim completion.

## Security Guidelines

Follow OWASP Top 10 principles in all code:

- **Access control:** deny by default; enforce least privilege; validate URLs for SSRF; prevent path traversal.
- **Cryptography:** use strong modern algorithms (Argon2/bcrypt for passwords, AES-256 at rest, HTTPS in transit); never hardcode secrets — read them from environment variables.
- **Injection:** use parameterized queries (no string-built SQL); use `shlex` for shell args; use `.textContent` / DOMPurify instead of raw `.innerHTML`.
- **Configuration:** disable debug/verbose errors in production; add security headers (CSP, HSTS, X-Content-Type-Options).
- **Authentication:** generate new session IDs on login; set `HttpOnly`, `Secure`, `SameSite=Strict` on cookies; rate-limit auth endpoints.
- **Deserialization:** prefer JSON over Pickle; validate/type-check before deserializing untrusted data.

When a security mitigation is applied, briefly state what it protects against. When reviewing code, explain the risk alongside the fix.

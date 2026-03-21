# Changelog

## [2.0.5] - 2026-03-21

### Fixed

- **CPU and memory sensors not updating** (`coordinator.py`): `_HWINFO_REFRESH_MULTIPLIER` was set to `60`, meaning CPU utilization and memory were only refreshed every 60 scan cycles. With a 10-second scan interval this resulted in updates only every ~10 minutes. The multiplier is now `1` so hwinfo refreshes on every scan cycle.
- **Memory used calculation** (`coordinator.py`): Reverted an incorrect earlier change that computed `memUsed` as `total − free`. On systems with aggressive kernel disk caching (e.g. Raspberry Pi), `memFree` is near zero while large amounts of memory are reclaimable, causing values like 93 % instead of the correct ~28 %. The integration now uses the OMV API's own `memUsed` field (`total − available`), which excludes reclaimable cache and matches what the OMV GUI displays.

## [2.0.4] - 2026-03-21

### Fixed

- **Memory usage percentage** (`coordinator.py`): `memUtilization` from the OMV API is a fraction (0–1) but was incorrectly used directly as a percentage. Multiplied by 100 so that e.g. `0.168` is now correctly displayed as `16.8 %`.
- **Memory used calculation** (`coordinator.py`): `memUsed` is now calculated as `memTotal − memFree` (consistent with `free -m` and hypervisor views like Proxmox) instead of the OMV API's `memUsed` field, which only counts application memory and excludes kernel cache/buffers.

### Added

- **"Memory total" sensor** (`sensor_types.py`, translations): New sensor showing total RAM in MB.
- **"Memory used" sensor** (`sensor_types.py`, translations): New sensor showing used RAM in MB (including kernel cache/buffers, consistent with `free -m`).

## [2.0.3] - 2026-03-19

### Build

- **GitHub Actions Node-24 migration** (`.github/workflows/ci.yml`, `.github/workflows/release.yml`): Bumped `actions/checkout` to `v6`, `actions/setup-python` to `v6`, and `codecov/codecov-action` to `v5` to remove the dependency on the deprecated Node 20 runtime. The test job now uses OIDC for Codecov on pushes and non-forked PRs; forked PRs remain on the tokenless path due to known GitHub and Codecov limitations.
- **Release automation** (`.github/workflows/ci.yml`, `.github/workflows/release.yml`): Version tags such as `v2.0.4` now also trigger CI. The CI calls the release workflow as a reusable workflow only when `lint`, `test`, and `hacs` all pass. The release workflow normalises the provided tag, checks out exactly that tag, and publishes the ZIP asset deterministically for the same release tag instead of relying on the event ref of a manual or UI-triggered run.
- **Release notes from changelog** (`.github/workflows/release.yml`, `CHANGELOG.md`): The release workflow now extracts the full section for the published version directly from `CHANGELOG.md` and passes it via `body_path` to the GitHub Release. If the version section is missing the workflow fails explicitly so no release is published with empty or generic notes.
- **Dependabot** (`.github/dependabot.yml`): New configuration for automated dependency-update PRs. Python packages are grouped into `test-dependencies` and `dev-tools`, GitHub Actions into `github-actions`. Weekly schedule on Mondays at 09:00 Europe/Berlin with `open-pull-requests-limit: 5`. No auto-merge — PRs must be merged manually.
- **Dependabot compatibility guardrails** (`.github/dependabot.yml`): Incompatible jumps to `pytest>=9`, `pytest-asyncio>=1`, `pytest-cov>=7`, `pytest-homeassistant-custom-component>=0.13.247`, and `pycares>=5` are ignored until a later Home Assistant upgrade. The repository therefore stays on the validated HA-2025.5 / PHCC-0.13.246 combination.
- **Platform baseline** (`pyproject.toml`, `.github/workflows/ci.yml`, `custom_components/omv/manifest.json`, `hacs.json`): Minimum versions raised to Python `>=3.13.2` and Home Assistant `>=2025.5.0`. The test stack now follows the stable Home Assistant 2025.5 line with `pytest-homeassistant-custom-component==0.13.246`, `homeassistant==2025.5.3`, `pytest==8.3.5`, `pytest-asyncio==0.26.0`, `pytest-cov==6.0.0`, and `pycares==4.11.0`.
- **Test bootstrap compatibility** (`pyproject.toml`): `pycares==4.11.0` pinned directly because `homeassistant==2025.5.3` currently pulls `aiodns==3.4.0`, and that combination fails at pytest plugin import under Python 3.13 with `pycares 5.x`.
- **Packaging** (`pyproject.toml`): Added setuptools build configuration so that `pip install -e ".[test]"` no longer fails due to accidental flat-layout autodiscovery of `reports` and `custom_components`.

### Fixed

- **aiohttp graceful shutdown** (`custom_components/omv/omv_api.py`): `OMVAPI.async_close()` now awaits one event-loop tick via `await asyncio.sleep(0)` after `await session.close()` so that aiohttp can run its deferred transport-cleanup callbacks while the loop is still active. This reduces platform- and version-dependent teardown errors involving `_run_safe_shutdown_loop` and makes shutdown more robust without relying solely on newer test pins.

### Security

- **CI hardening** (`.github/workflows/ci.yml`): Replaced `hacs/action@main` with the immutable release commit `d556e736723344f83838d08488c983a15381059a` of HACS action `22.5.0`. A mutable `main` ref allows supply-chain attacks where compromised upstream code can be executed in CI; the previously tested ref `hacs/action@v2` does not exist in the upstream repository (OWASP A01/A08).

## [2.0.2] - 2026-03-18

### Fixed

- **Docker container icon** (`sensor_types.py`): Reverted `docker_container_not_running` icon to `mdi:docker` — `mdi:docker-off` does not exist in the Material Design Icons set and caused missing icons in the HA frontend.
- **Lint** (`diagnostics.py`): Removed spurious extra whitespace before an inline comment (Ruff E262).
- **HACS manifest** (`manifest.json`): Added required `issue_tracker` field to pass HACS integration manifest validation (previously caused 2/8 checks to fail).
- **Lingering aiohttp thread in tests** (`omv_api.py`): Removed the manually managed `TCPConnector` instance. `ClientSession` now creates and owns its default connector so that session and connector are closed atomically via a single `await session.close()` call, preventing aiohttp from spawning a `_run_safe_shutdown_loop` background thread during pytest teardown. SSL options previously set on the connector are now passed per-request via the `ssl=` parameter.

### Changed

- **HACS Default** (`info.md`): Badge updated from *HACS Custom* to *HACS Default* following acceptance into the HACS Default Store.
- **readme** (`readme.md`): Changed header image of OMV logo and added a info

### Internal

- **pytest config** (`pyproject.toml`): Set `asyncio_default_fixture_loop_scope = "function"` to silence the `pytest-asyncio` deprecation warning about the unset fixture loop scope.

## [2.0.1] - 2026-03-18

### Security

- **Path traversal mitigation** (`coordinator.py`): Background-task filenames returned by the OMV server are now validated via `_sanitize_background_path()`. Paths containing `..` segments are rejected with a warning log, preventing a compromised or malicious OMV instance from injecting traversal sequences (e.g. `../../etc/shadow`) into `Exec.getOutput` calls (OWASP A01/A03).
- **Diagnostics data redaction** (`diagnostics.py`): The `TO_REDACT` set now additionally covers `serialnumber`, `address`, `netmask`, `gateway`, `macaddress`, and `mac` to prevent hardware identifiers and network topology details from being included in unredacted HA diagnostics exports.
- **Assert replaced by explicit guard** (`coordinator.py`): `assert last_error is not None` in `async_execute_compose_command` was replaced by an explicit `if`-check that raises `OMVApiError`. Python's optimised mode (`-O`) silently strips `assert` statements, which would have caused an unhandled `UnboundLocalError` in production deployments.

## [2.0.0] - 2026-03-17

### Added


- Complete async rewrite of the OMV integration under the new domain omv
- aiohttp based JSON-RPC client with session reauthentication
- DataUpdateCoordinator based polling architecture
- Button entities for reboot and shutdown
- Optional ZFS pool monitoring
- Automated tests for API, config flow, coordinator, sensors, binary sensors, and buttons
- Per-resource device modeling for disks, RAIDs, filesystems, and ZFS pools instead of exposing storage only on the OMV hub
- Options flow selectors for disks, filesystems, services, network interfaces, RAIDs, and ZFS pools
- Virtual passthrough option for hypervisor-backed disks that disables SMART polling and temperature entities
- Disk capacity sensors for total, used, free, and percentage values in decimal GB units
- Filesystem capacity sensors for total, used, free, and percentage values in decimal GB units
- Dedicated Docker container summary sensors for total, running, and not-running containers
- Dedicated Docker container devices with per-container state, status, created, and started sensors plus optional Compose project grouping and selection
- RAID health reporting and RAID level metadata on RAID-backed devices and entities
- Localized dynamic entity names that follow the active Home Assistant language for disks, filesystems, networks, RAIDs, ZFS pools, and services
- OMV8-aware ZFS storage mapping that can attach pool-style filesystems and ZFS pools to their backing disk devices via mountpoint and size correlation
- Expanded automated coverage for resource selection, storage mapping, passthrough handling, and device naming
- SMART `getAttributes` is no longer called for hotpluggable (USB/removable) disks, preventing spurious API errors on OMV 7 setups with USB storage attached
- Memory usage sensor now prefers the `memUtilization` field delivered directly by the OMV API and falls back to the calculated `memUsed / memTotal` ratio only when the field is absent


### Changed

- The integration domain changed from openmediavault to omv
- Filesystems and ZFS pools now attach to the most specific storage device when a matching disk or logical device can be identified
- RAID and logical md devices are now synthesized from filesystem metadata when OMV does not expose them as standalone disks
- Disk device names and metadata are now clearer for both physical disks and logical RAID devices in the Home Assistant device registry
- Hub sensors now expose richer hardware metadata such as CPU model and kernel version
- Entity icons were added or refined across system, storage, service, binary sensor, and button entities
- Entity updates now use CoordinatorEntity patterns
- Project validation now runs through pyproject.toml, Ruff, and pytest
- The config flow validates OMV connectivity asynchronously

### Removed
- Synchronous requests based transport
- Legacy controller, parser, helper, and dispatcher entity update model
- Cookie persistence on disk
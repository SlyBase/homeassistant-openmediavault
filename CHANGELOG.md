# Changelog

## [2.2.2] - 2026-05-19

### Added

- **Apply Configuration button**: New button entity (`button.<host>_apply_config`) that applies all pending OMV configuration changes directly from Home Assistant. The integration refreshes automatically before and after the call, and any "configuration pending" notification is dismissed on success.
- **Reboot safety check**: The Reboot button now verifies there are no unapplied configuration changes before rebooting. If pending changes exist, the reboot is blocked and the error message lists the affected OMV modules.
- **Post-update notification**: After successfully installing package updates, if configuration changes are pending, a persistent Home Assistant notification prompts you to press Apply Configuration before rebooting.

### Fixed

- **Apply Configuration button always failed on OMV 8**: A required parameter was missing in the API call, causing the button to always show an error. The button now works correctly on both OMV 7 and OMV 8.
- **Package installation incorrectly blocked when configuration was dirty**: Installing packages was unnecessarily blocked when OMV had pending configuration changes. Package installation is independent of the configuration state — the reboot guard is the correct safeguard.
- **"What's new" now shows full package details**: Pressing **What's new** on the Home Assistant update card now shows the complete list of pending packages with name, version, and — when available — a short description.
- **Configuration-related error messages are now translated**: Error messages shown when installation or reboot is blocked by pending configuration changes are now fully localized.

### Changed

- **Update card shows a compact summary**: The update card preview shows up to two package names/versions followed by a `… +N more` suffix (max 200 characters). Full details are available via **What's new**.
- **OMV HTTP 500 errors include the response body in the HA log**: When OMV returns an HTTP 500 error, the first 500 characters of the response body are now appended to the log entry, making the root cause easier to diagnose.

## [2.2.1] - 2026-05-18

### Added

- **Dark-mode brand images** (`brand/dark_icon.png`, `brand/dark_logo.png`): Added `dark_icon.png` and `dark_logo.png` to the `brand/` directory so HACS and the HA frontend can display the integration icon correctly in dark mode.

## [2.2.0] - 2026-05-12

### Added

- **OMV System Update entity** (`update.py`, `const.py`, translations): New `update` platform entity (`update.<host>_system_update`) that exposes the OMV package update status as a native Home Assistant update card. The entity reports the installed OMV version as `installed_version`; when package updates are pending, `latest_version` is set to a synthetic string (e.g. `7.7.24-7 (+3 packages)`) so HA transitions the entity state to `on`. The `release_url` points directly to the OMV update management page (`/#/system/updatemgmt/updates`) so users can open the relevant screen with a single click from the HA update card. The **Install** button mirrors the OMV web UI upgrade workflow: `Apt.update` refreshes the apt cache, then `Apt.upgrade` runs `apt-get dist-upgrade`. Both steps are tracked via `Exec.isRunning` so `async_install()` blocks until each background process finishes (or a 10-minute timeout expires per step), keeping HA's `in_progress` spinner visible for the full duration. Errors reported by OMV (e.g. non-zero exit code from apt-get) propagate as exceptions so HA can display a failure notification.
- **`release_summary` in update entity** (`update.py`, `coordinator.py`): The update entity's **More Info** card now lists every pending package with its name, new version, description, maintainer, homepage, source repository and installed size. Details are fetched from OMV via `Apt.getUpgradedList` (reads the local apt cache — no network access). Each package is rendered as a compact block and the blocks are joined by a blank line, matching the information shown in the OMV web UI update list.
- **`reboot_required` on update entity** (`update.py`): The update entity now surfaces the OMV host's pending-reboot state. The `extra_state_attributes` dict includes `reboot_required: true/false` (readable in automations and dashboards). When a reboot is pending but no further package updates are available, `latest_version` is set to `<version> (reboot required)` so the update card stays active (state `on`) until the host is rebooted. Pressing the **Install** button in this state calls `System.reboot` on OMV instead of the apt workflow — the button acts as a reboot trigger. Use the existing `button.<host>_reboot` entity as an alternative.

### Removed

- **`binary_sensor.update_available`** (`binary_sensor_types.py`): The `update_available` binary sensor (`BinarySensorDeviceClass.UPDATE`) is superseded by the new `update` platform entity. Existing instances are automatically removed from the HA entity registry on the next integration reload via `_async_cleanup_stale_registry_entries`.

## [2.1.3] - 2026-05-01

### Fixed

- **Duplicate RAID sensors** (Issue #27) (`coordinator.py`, `sensor_types.py`): Fixed a critical bug where md RAID arrays with multiple member disks (e.g., md0 composed of sda + sdb) would create duplicate sensor records. The `_normalize_raids()` function now deduplicates by RAID device name and groups all member disks under a single RAID record. New helper method `_extract_raid_device()` reliably extracts RAID device names from disk records by checking direct OMV fields, parsing descriptions, and extracting from device paths. Member disk tracking is now exposed in sensor attributes via `member_disks` field.

- **Duplicate disk sensors for md RAID arrays** (Issue #27, follow-up) (`coordinator.py`): Fixed a second cause of duplicate sensor entries where OMV 8 returns the md device `devicename` with a `/dev/` prefix (e.g. `/dev/md0` instead of `md0`). `_normalize_disks()` now normalizes the device name by stripping the `/dev/` prefix before using it as `disk_key`. `_augment_disks_with_logical_storage()` also normalizes existing `disk_key` values before deduplication, so the synthetic fallback entry is no longer created when the real md device is already in the list.

- **Connection instability and unavailable entities** (Issue #26) (`omv_api.py`, `coordinator.py`): Implemented comprehensive connection recovery with exponential backoff retry logic to handle transient connection failures introduced by OMV 8.2.10-1. The `async_call()` method now automatically retries failed requests up to 3 times with 1s, 2s, and 4s delays, and attempts session re-establishment between retries. Additionally, `_async_update_data()` now caches the last valid dataset and uses it as a fallback when API errors occur, preventing sensors from going unavailable during temporary connection glitches. This ensures entities remain available across OMV service restarts and network hiccups without requiring a manual reload of the integration.

- **Duplicate network sensors** (Issue #27 follow-up) (`coordinator.py`): Fixed "Platform omv does not generate unique IDs" errors for network TX/RX sensors caused by `Network.enumerateDevices` returning the same interface UUID more than once (observed on OMV 8 with bond/VLAN setups). `_normalize_network()` now deduplicates by UUID and logs skipped duplicates at DEBUG level.

- **Duplicate disk sensors** (`coordinator.py`): `_normalize_disks()` now deduplicates by `devicename` to guard against OMV installations that return the same physical device multiple times from `DiskMgmt.enumerateDevices`.

- **Debug diagnostics** (`coordinator.py`): Added `_LOGGER.debug()` log entries in `_normalize_network`, `_normalize_disks`, and `_normalize_raids` to help diagnose future duplicate-entity issues. Enable `custom_components.omv: debug` in `configuration.yaml` to see full deduplication traces.

- **SMART `getAttributes` log spam** (`coordinator.py`, `omv_api.py`): Disks that do not support ATA SMART attributes (e.g. NVMe drives, certain SATA controllers) caused OMV to return HTTP 500, which triggered the full 3-retry/7-second backoff loop and four log messages on every coordinator poll (every 60 s). Fixed in two layers: (1) `async_call()` now accepts a `max_retries` keyword argument, allowing callers to opt out of retries; (2) the coordinator passes `max_retries=0` for `Smart.getAttributes` and records failing device paths in `_smart_no_attributes` — those devices are silently skipped on all subsequent polls.

## [2.1.2] - 2026-04-17

### Changed

- **Dependabot assignees and ignore rules** (`.github/dependabot.yml`): Added `slydlake` as assignee for both `pip` and `github-actions` ecosystems so dependency PRs trigger notifications. Added ignore rules for `pytest`, `pytest-cov`, and `pytest-asyncio` which are transitively pinned by `pytest-homeassistant-custom-component` and must only be updated together with PHCC.
- **Missing Dependabot labels** (GitHub): Created `dependencies`, `python`, and `github-actions` labels in the repository to match the label configuration in `dependabot.yml`.

### Added

- **Dependency updates in release notes** (`.github/release.yml`, `.github/workflows/release.yml`): Replaced the custom `gh pr list` step with GitHub's native auto-generated release notes (`generateReleaseNotes`). Merged PRs are now automatically categorized by label (Features, Bug Fixes, Dependencies) via `.github/release.yml`.

## [2.1.1] - 2026-04-16

### Fixed

- **Standalone device for unmapped filesystems** (`entity.py`): Filesystems without a parent disk (e.g. mergerfs, FUSE mounts, NFS/CIFS shares) now get their own dedicated device in Home Assistant instead of being silently attached to the main OMV hub device. A new `get_filesystem_device_identifier()` and `_build_standalone_filesystem_device_info()` create a proper device with the filesystem label, type, and UUID. Disk-backed filesystems continue to map to their parent disk device as before.
- **Virtual filesystem size-based disk matching** (`coordinator.py`): Virtual and network-backed filesystems (mergerfs, NFS, CIFS, SSHFS, overlay, and any FUSE mount) are no longer incorrectly matched to a physical disk via the 8% size tolerance fallback. Previously, a mergerfs pool whose aggregated size happened to be close to a physical disk's size would be silently attached to that disk device.
- **Standalone filesystem device naming** (`entity.py`): Virtual filesystem devices now follow the same naming pattern as other devices — e.g. `Mergerfs (mergerfs_test)`, `NFS (share-name)`, `CIFS (backup)` instead of the generic `Filesystem` prefix.

## [2.1.0] - 2026-04-10

### Changed

- **Test stack upgrade to HA 2026.2** (`pyproject.toml`, `manifest.json`, `hacs.json`, `.github/dependabot.yml`): Bumped test dependencies to `pytest==9.0.0`, `pytest-asyncio==1.3.0`, `pytest-cov==7.0.0`, `pytest-homeassistant-custom-component==0.13.316` (which pulls `homeassistant==2026.2.3`), and `pycares==5.0.1` (required by `aiodns==4.0.0` bundled with HA 2026.2). The minimum supported Home Assistant version in `manifest.json` and `hacs.json` is raised to `2026.2.3`. All previous Dependabot ignore rules for `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-homeassistant-custom-component`, and `pycares` have been removed from `.github/dependabot.yml`.

### Added

- **Manifest consistency tests** (`tests/test_manifest.py`): New test module that validates `manifest.json` structure (required fields, domain, version formats) and asserts that `hacs.json` and `manifest.json` declare identical minimum Home Assistant versions — preventing silent version drift between the two files.
- **Release workflow Node-24 readiness** (`.github/workflows/release.yml`): Switched from `softprops/action-gh-release@v2` (Node 20 runtime) to `ncipollo/release-action` pinned to immutable commit `339a81892b84b4eeb0f6e744e4574d79d0d9b8dd` (`v1.21.0`, Node 24 runtime), preserving tag-based release body and asset upload behavior.
- **Dependabot PR titles** (`.github/dependabot.yml`): Removed grouped version updates for `pip` and `github-actions` because Dependabot currently formats grouped pull request titles for single-directory repos as `... group across 1 directory with N updates` and does not offer a repository-side title override. Future updates will be raised as individual pull requests again.

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
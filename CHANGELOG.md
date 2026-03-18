# Changelog

## [2.0.3] - 2026-03-19

### Build

- **GitHub Actions Node-24 migration** (`.github/workflows/ci.yml`, `.github/workflows/release.yml`): `actions/checkout` auf `v6`, `actions/setup-python` auf `v6` und `codecov/codecov-action` auf `v5` angehoben, damit die Workflows nicht mehr auf das abgekündigte Node-20-Runtime angewiesen sind. Der Test-Job nutzt für Codecov jetzt OIDC auf Pushes und nicht-geforkten PRs; Fork-PRs bleiben wegen bekannter GitHub- und Codecov-Einschränkungen beim tokenlosen Pfad.
- **Release automation** (`.github/workflows/ci.yml`, `.github/workflows/release.yml`): Versions-Tags wie `v2.0.4` triggern jetzt ebenfalls die CI. Nur wenn `lint`, `test` und `hacs` erfolgreich sind, ruft die CI den Release-Workflow als wiederverwendbaren Workflow auf. Der Release-Workflow normalisiert den übergebenen Tag, checkt genau diesen Tag aus und veröffentlicht das ZIP-Asset deterministisch für denselben Release-Tag statt sich auf den Event-Ref des manuellen oder UI-basierten Starts zu verlassen.
- **Dependabot** (`.github/dependabot.yml`): Neue Konfiguration für automatische Dependency-Update-PRs. Python-Pakete in Gruppen `test-dependencies` und `dev-tools`, GitHub Actions in `github-actions`. Wöchentlicher Zeitplan montags 09:00 Europe/Berlin mit `open-pull-requests-limit: 5`. Kein Auto-Merge — PRs müssen manuell gemergt werden.
- **Dependabot compatibility guardrails** (`.github/dependabot.yml`): Inkompatible Sprünge auf `pytest>=9`, `pytest-asyncio>=1`, `pytest-cov>=7`, `pytest-homeassistant-custom-component>=0.13.247` und `pycares>=5` werden bis zu einer späteren Home-Assistant-Anhebung ignoriert. Der aktuelle Repo-Stand bleibt damit auf der validierten HA-2025.5-/PHCC-0.13.246-Kombination.
- **Platform baseline** (`pyproject.toml`, `.github/workflows/ci.yml`, `custom_components/omv/manifest.json`, `hacs.json`): Mindestversionen auf Python `>=3.13.2` und Home Assistant `>=2025.5.0` angehoben. Der Test-Stack folgt jetzt der stabilen Home-Assistant-2025.5-Linie mit `pytest-homeassistant-custom-component==0.13.246`, `homeassistant==2025.5.3`, `pytest==8.3.5`, `pytest-asyncio==0.26.0`, `pytest-cov==6.0.0` und `pycares==4.11.0`.
- **Test bootstrap compatibility** (`pyproject.toml`): `pycares==4.11.0` direkt gepinnt, weil `homeassistant==2025.5.3` aktuell `aiodns==3.4.0` zieht und diese Kombination unter Python 3.13 mit `pycares 5.x` bereits beim pytest-Plugin-Import scheitert.
- **Packaging** (`pyproject.toml`): Setuptools-Build-Konfiguration ergänzt, damit `pip install -e ".[test]"` nicht mehr an einer versehentlichen Flat-Layout-Autodiscovery von `reports` und `custom_components` scheitert.

### Fixed

- **aiohttp graceful shutdown** (`custom_components/omv/omv_api.py`): `OMVAPI.async_close()` wartet nach `await session.close()` einen Event-Loop-Tick mit `await asyncio.sleep(0)`, damit aiohttp seine verzögerten Transport-Cleanup-Callbacks noch innerhalb des aktiven Loops ausführen kann. Das reduziert plattform- und versionsabhängige Teardown-Fehler mit `_run_safe_shutdown_loop` und macht den Shutdown robuster auch ohne reine Abhängigkeit von neueren Test-Pins.

### Security

- **CI-Härtung** (`.github/workflows/ci.yml`): `hacs/action@main` durch den unveränderlichen Release-Commit `d556e736723344f83838d08488c983a15381059a` der HACS-Action `22.5.0` ersetzt. Ein mutierbarer `main`-Ref erlaubt Supply-Chain-Angriffe, bei denen kompromittierter Upstream-Code in der CI ausgeführt werden kann, und der zwischenzeitlich getestete Ref `hacs/action@v2` existiert im Upstream-Repository nicht (OWASP A01/A08).

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
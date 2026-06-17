# OpenMediaVault (OMV) for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?logo=github)](https://github.com/sponsors/slydlake)

Monitor and control your OpenMediaVault NAS from Home Assistant.

> **Note:** This is an independent community project and is **not** affiliated with or endorsed by the OpenMediaVault project or its developers.

![OpenMediaVault Logo](https://raw.githubusercontent.com/slybase/homeassistant-openmediavault/main/docs/assets/images/ui/header.svg)

## About

This integration is a full modernization of [tomaae/homeassistant-openmediavault](https://github.com/tomaae/homeassistant-openmediavault), building on the groundwork laid by [cneuen/homeassistant-openmediavault](https://github.com/cneuen/homeassistant-openmediavault). Selected compatibility improvements from [Boci-HA/homeassistant-openmediavault](https://github.com/Boci-HA/homeassistant-openmediavault) have also been incorporated.

The original integration relied on a synchronous, poll-based controller that no longer fits the current Home Assistant architecture. This fork replaces that foundation with a native async client, a `DataUpdateCoordinator`-driven update cycle, and a platform structure aligned with modern HA conventions. Alongside that architectural shift, the scope of what the integration monitors has grown substantially: per-resource device modeling, Docker Compose support, ZFS pool awareness, RAID synthesis, dynamic localized entity names, capacity sensors, and a richer options flow are all new additions rather than ports of existing functionality.

## Features

- Async OMV JSON-RPC client based on aiohttp
- DataUpdateCoordinator architecture for predictable polling and updates
- CPU, memory, temperature, filesystem, disk, SMART, network, RAID, and optional ZFS monitoring
- Per-resource device modeling — disks, RAIDs, filesystems, ZFS pools, and Docker containers each appear as separate HA devices
- Docker Compose support with per-container state, version, and lifecycle button entities
- Native Home Assistant update entity with pending package count, per-package release details in the More Info dialog, a direct link to the OMV updates page, one-click package installation, and reboot-only mode when no further packages are pending
- Binary sensor for reboot requirement and OMV service health
- Reboot and shutdown buttons
- Localized dynamic entity names that follow the active HA language

## Supported Versions

- OpenMediaVault 7 and 8
- Home Assistant 2025.5 or newer
- Python 3.13.2 or newer for local development and CI

The active integration domain is omv.

## Screenshots

<img src="https://raw.githubusercontent.com/slybase/homeassistant-openmediavault/main/docs/assets/images/ui/omv-system.png" alt="OMV Sensors" height="600">
<img src="https://raw.githubusercontent.com/slybase/homeassistant-openmediavault/main/docs/assets/images/ui/omv-container.png" alt="Container" height="400">
<img src="https://raw.githubusercontent.com/slybase/homeassistant-openmediavault/main/docs/assets/images/ui/omv-disk.png" alt="Disk" height="600">
<img src="https://raw.githubusercontent.com/slybase/homeassistant-openmediavault/main/docs/assets/images/ui/omv-raid.png" alt="Raid" height="600">
<img src="https://raw.githubusercontent.com/slybase/homeassistant-openmediavault/main/docs/assets/images/ui/omv-cards.png" alt="Card samples" width="600">

## Installation

### Install using HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=slybase&repository=homeassistant-openmediavault&category=integration)

To add manually: open **HACS** > **Integrations** > three-dot menu > **Custom repositories** > add `https://github.com/slybase/homeassistant-openmediavault` (category: Integration) > **Download**.

After installing, restart Home Assistant. Then open **Settings** > **Devices & Services** > **Add Integration** and search for **OpenMediaVault**.

### Install manually

Copy `custom_components/omv/` from this repository into your HA `config/custom_components/` directory. Restart Home Assistant, then add the integration as described above.

## Setup

The config flow asks for:

- Host
- Username
- Password
- Port
- SSL
- SSL verification

## Configuration

After setup, the options flow lets you adjust:

- The scan interval
- The SMART polling interval — how often SMART data (status, attributes, disk temperature) is read. Reading SMART wakes disks from standby, so a larger interval lets disks spin down for power saving. Defaults to the scan interval.
- Whether SMART polling is disabled entirely — disks are then never woken by the integration, but SMART status, SMART attributes, and disk temperature entities become unavailable
- Which disks, filesystems, network interfaces, services, RAIDs, ZFS pools, Compose projects, and containers are monitored — resources not selected simply disappear from Home Assistant

## Entities

### System (hub device)

- CPU utilization, memory usage, CPU temperature, uptime, available package updates
- Intel iGPU load and current frequency (when available via sysfs)
- Docker container summary: total, running, and not-running counts
- **Update entity**: exposes OMV updates as a native Home Assistant update card with the installed OMV version, a synthetic latest version while packages or a reboot are pending, per-package release details in the **More Info** dialog, and a direct link to the OMV updates page; the **Install** button runs `apt update` + `apt upgrade` on OMV and waits for completion, and when only a reboot is pending (no further packages), pressing **Install** triggers a system reboot instead
- Binary sensor: reboot required
- Buttons: Reboot, Shutdown

### Disk devices (one device per physical disk and logical RAID/md device)

- Temperature
- Used %, free %, used size, free size, total size
- SMART status and SMART attributes (Raw Read Error Rate, Reallocated Sector Count, Pending Sector Count, Uncorrectable Sector Count, Power On Hours, Start Stop Count, Load Cycle Count)

### Filesystem devices (one device per mounted filesystem)

- Used %, free %, used size, free size, total size

### Network interface devices

- TX rate (Mbps), RX rate (Mbps)

### RAID devices

- RAID health status

### ZFS pool devices

- Pool status

### TempMon sensors (requires `openmediavault-tempmon`)

The [`openmediavault-tempmon`](https://github.com/openmediavault-plugin-developers/openmediavault-tempmon) plugin lets you expose any temperature sensor on the OMV host to Home Assistant. Each configured sensor becomes its own HA entity (named after the sensor name you set in OMV).

**Use case: ARM CPU temperature (e.g. Rock5 ITX / Armbian)**

OMV's built-in CPU temperature sensor reads from `thermal_zone0`, which returns 0 on many ARM SoCs. Install `openmediavault-tempmon` via OMV-Extras and configure a custom sensor:

| Field | Value |
|---|---|
| Name | `CPU Temp` (or any label) |
| Script path | `/usr/sbin/cpu-temp` |
| Script | `cat /sys/class/hwmon/hwmon0/temp1_input` |
| Divisor | `1000` (sensor reports millidegrees) |

Once saved in OMV, the integration picks up the sensor automatically on the next coordinator refresh. No integration restart required.

> **Tip:** The correct hwmon path varies by board. Check `/etc/armbianmonitor/datasources/soctemp` on Armbian systems — it is a symlink to the right input file.

### OMV service devices

- Binary sensor: service running / not running

### Docker Compose project devices (one device per project)

- Project status, total containers, running containers, not-running containers
- Buttons: `compose up`, `compose down`, `start`, `stop`, `pull`

### System-wide Docker buttons (on the hub device)

- `docker image prune`, `docker container prune`

### Container devices (one device per container)

- State, status detail, created timestamp, started timestamp, image version
- Volume size (when reported by OMV)

## Development

### Environment

Install the local test and development dependencies with:

```bash
pip install -e ".[test,dev]"
```

### Local Validation

```bash
.venv/bin/python -m ruff check custom_components tests
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests --cov=custom_components/omv --cov-report=term-missing
```

### Debug Logging

```yaml
logger:
  default: info
  logs:
    custom_components.omv: debug
```

## Compatibility Notes

See docs/omv-rpc-compatibility.md for the current RPC compatibility summary and the live probe workflow for validating OMV7 and OMV8 side by side.
# OpenMediaVault (OMV) for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Monitor and control your OpenMediaVault NAS from Home Assistant.

![OpenMediaVault Logo](docs/assets/images/ui/header.png)

## Features

- Async OMV JSON-RPC client based on aiohttp
- DataUpdateCoordinator architecture for predictable polling and updates
- CPU, memory, temperature, filesystem, disk, SMART, network, RAID, and optional ZFS monitoring
- Binary sensors for package updates, reboot requirement, and OMV services
- Reboot and shutdown buttons

## Supported Versions

- OpenMediaVault 7 and 8
- Home Assistant 2024.8 or newer

The active integration domain is omv.

## Screenshots

![Filesystem usage](docs/assets/images/ui/filesystem_sensor.png)
![System sensors](docs/assets/images/ui/system_sensors.png)
![Disk sensor](docs/assets/images/ui/disk_sensor.png)
![Add Integration](docs/assets/images/ui/setup_integration.png)

## Installation With HACS

1. Open HACS.
2. Go to Integrations.
3. Add the custom repository https://github.com/SlyBase/homeassistant-openmediavault.
4. Install OpenMediaVault (OMV).
5. Restart Home Assistant.
6. Add the OMV integration from Settings, Devices & Services.

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
- Whether SMART polling should be disabled

## Entities

The integration currently provides:

- System sensors for CPU utilization, memory usage, CPU temperature, and uptime
- Disk temperature sensors with SMART-related attributes
- Filesystem usage sensors
- Network RX and TX rate sensors
- RAID status sensors from /proc/mdstat when available
- Optional ZFS pool status sensors when the ZFS plugin is installed
- Binary sensors for update availability, reboot requirement, and OMV services
- Buttons for reboot and shutdown

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
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz)

Monitor and control your OpenMediaVault NAS from Home Assistant.

## Features

- Async OMV JSON-RPC client — supports OMV 7 and OMV 8
- CPU, memory, temperature, uptime, and package update monitoring
- Intel iGPU load and frequency sensors (when available)
- Disk, filesystem, RAID, and optional ZFS pool monitoring with capacity sensors
- SMART status and attribute sensors per disk
- Network interface TX/RX rate sensors (Mbps)
- OMV service binary sensors (running / not running)
- Docker container summary sensors and per-container state, version, and lifecycle buttons
- Docker Compose project devices with `up`, `down`, `start`, `stop`, and `pull` buttons
- System-wide `docker image prune` and `docker container prune` buttons
- Per-resource device modeling — disks, RAIDs, filesystems, ZFS pools, and containers each appear as separate HA devices
- Binary sensors for package updates and reboot requirement
- Reboot and shutdown buttons
- Resource filtering via options flow — only selected disks, filesystems, networks, services, RAIDs, ZFS pools, and containers are monitored
- Virtual passthrough mode for hypervisor-backed setups (disables SMART and temperature entities)
- Localized dynamic entity names that follow the active HA language

## Supported Versions

- OpenMediaVault 7 and 8
- Home Assistant 2025.5 or newer

## Links

- [Documentation](https://github.com/slybase/homeassistant-openmediavault)
- [Configuration](https://github.com/slybase/homeassistant-openmediavault#configuration)
- [Issues](https://github.com/slybase/homeassistant-openmediavault/issues)

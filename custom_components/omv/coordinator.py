"""DataUpdateCoordinator for OpenMediaVault."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SELECTED_COMPOSE_PROJECTS,
    CONF_SELECTED_CONTAINERS,
    CONF_SELECTED_DISKS,
    CONF_SELECTED_FILESYSTEMS,
    CONF_SELECTED_NETWORK_INTERFACES,
    CONF_SELECTED_RAIDS,
    CONF_SELECTED_SERVICES,
    CONF_SELECTED_ZFS_POOLS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .exceptions import OMVApiError, OMVConnectionError
from .omv_api import OMVAPI

_LOGGER = logging.getLogger(__name__)
_HWINFO_REFRESH_MULTIPLIER = 1
_COMPOSE_LIST_PARAMS = {"start": 0, "limit": 999}
_COMPOSE_BG_VOLUME_PARAMS = {
    "start": 0,
    "limit": -1,
    "sortdir": "asc",
    "sortfield": "name",
}
_CONTAINER_STATE_TO_PROJECT_STATUS = {
    "created": "DOWN",
    "dead": "DOWN",
    "exited": "STOPPED",
    "paused": "STOPPED",
    "restarting": "UP",
    "running": "UP",
}
_SMART_ATTRIBUTE_NAMES = (
    "Raw_Read_Error_Rate",
    "Spin_Up_Time",
    "Start_Stop_Count",
    "Reallocated_Sector_Ct",
    "Seek_Error_Rate",
    "Load_Cycle_Count",
    "UDMA_CRC_Error_Count",
    "Multi_Zone_Error_Rate",
)
_GPU_CUR_FREQ_PATH = "/sys/class/drm/card0/gt_cur_freq_mhz"
_GPU_MAX_FREQ_PATH = "/sys/class/drm/card0/gt_max_freq_mhz"
_GPU_CONFIRMATION_CYCLES = 2


class OMVDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and normalize data from OpenMediaVault."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: OMVAPI,
        *,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        smart_disabled: bool = False,
        virtual_passthrough: bool = False,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.config_entry = config_entry
        self.api = api
        self.omv_version = 0
        self.smart_disabled = smart_disabled or virtual_passthrough
        self.virtual_passthrough = virtual_passthrough
        self._hwinfo: dict[str, Any] = {}
        self._hwinfo_counter = 0
        self._network_counters: dict[str, dict[str, float]] = {}
        self._inventory_source: dict[str, Any] = {}
        self._last_stable_gpu: dict[str, Any] = {}
        self._gpu_load_counter: int = 0

    async def async_init(self, system_info: dict[str, Any]) -> None:
        """Initialize version metadata from the initial connect response."""
        self._hwinfo = self._normalize_hwinfo(system_info)
        self._hwinfo_counter = _HWINFO_REFRESH_MULTIPLIER
        version_str = str(self._hwinfo.get("version", "0"))
        match = re.match(r"(\d+)", version_str)
        self.omv_version = int(match.group(1)) if match else 0
        _LOGGER.info(
            "OMV %s detected (major: %d) at %s",
            version_str,
            self.omv_version,
            self.api.base_url,
        )

    async def async_execute_compose_command(self, params: dict[str, Any]) -> Any:
        """Execute a compose command and surface background-task output in logs."""
        response: Any = None
        last_error: OMVApiError | OMVConnectionError | None = None
        for service in ("Compose", "compose"):
            try:
                response = await self.api.async_call(service, "doCommand", params)
                _LOGGER.debug(
                    "Compose command %s.doCommand params=%s response=%s",
                    service,
                    params,
                    response,
                )
                break
            except (OMVApiError, OMVConnectionError) as err:
                last_error = err
                _LOGGER.debug(
                    "Compose command %s.doCommand failed params=%s error=%s",
                    service,
                    params,
                    err,
                )
        else:
            if last_error is None:
                raise OMVApiError("No Compose service available")
            raise last_error

        filename = self._extract_background_filename(response)
        if filename:
            output = await self._async_read_exec_output(filename)
            if output:
                _LOGGER.debug(
                    "Compose command background output filename=%s output=%s",
                    filename,
                    output,
                )

        return response

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all coordinator data from OMV."""
        try:
            self._hwinfo_counter += 1
            if self._hwinfo_counter >= _HWINFO_REFRESH_MULTIPLIER or not self._hwinfo:
                self._hwinfo = await self._async_get_hwinfo()
                self._hwinfo_counter = 0

            raw_filesystems = await self._fetch_or_empty("FileSystemMgmt", "enumerateFilesystems")
            raw_services = await self._fetch_or_empty("Services", "getStatus")
            raw_network = await self._fetch_or_empty("Network", "enumerateDevices")
            raw_disks = await self._fetch_or_empty("DiskMgmt", "enumerateDevices")

            disks = self._normalize_disks(raw_disks)
            disks = self._augment_disks_with_logical_storage(disks, raw_filesystems)
            filesystems = self._normalize_filesystems(raw_filesystems, disks)
            services = self._normalize_services(raw_services)
            raids = self._normalize_raids(disks)
            compose = self._normalize_compose(
                await self._fetch_optional(
                    "compose",
                    "getContainerList",
                    _COMPOSE_LIST_PARAMS,
                )
            )
            compose = self._merge_compose_inspect(
                compose,
                await self._fetch_compose_inspect(
                    self._compose_inspect_targets(compose),
                ),
            )
            compose_projects = self._normalize_compose_projects(
                await self._fetch_optional(
                    "compose",
                    "getFileList",
                    _COMPOSE_LIST_PARAMS,
                ),
                compose,
            )
            compose = self._link_compose_projects(compose, compose_projects)
            compose_volumes = self._normalize_compose_volumes(
                await self._fetch_optional_background_json(
                    "Compose",
                    "getVolumesBg",
                    _COMPOSE_BG_VOLUME_PARAMS,
                ),
                compose,
            )
            zfs_pools = self._normalize_zfs_pools(
                await self._fetch_optional("zfs", "listPools"),
                filesystems,
                disks,
            )
            self._apply_storage_metrics(disks, filesystems, zfs_pools)

            smart_records: list[dict[str, Any]] = []
            if not self.smart_disabled and not self.virtual_passthrough:
                smart_records = await self._async_get_smart(disks)

            gpu = await self._async_get_gpu_info()

            unfiltered_data: dict[str, Any] = {
                "hwinfo": self._hwinfo,
                "disk": disks,
                "fs": filesystems,
                "service": services,
                "network": self._normalize_network(raw_network),
                "smart": smart_records,
                "compose": compose,
                "compose_projects": compose_projects,
                "compose_summary": self._summarize_compose(compose, services),
                "compose_volumes": compose_volumes,
                "kvm": self._normalize_named_collection(
                    await self._fetch_optional("Kvm", "getVmList", {"start": 0, "limit": 999})
                ),
                "zfs": zfs_pools,
                "raid": raids,
                "gpu": gpu,
            }

            self._inventory_source = unfiltered_data
            return self.filter_data_by_selection(
                unfiltered_data,
                dict(self.config_entry.options),
            )
        except OMVConnectionError as err:
            raise UpdateFailed(f"Cannot connect to OMV: {err}") from err
        except OMVApiError as err:
            raise UpdateFailed(f"OMV API error: {err}") from err

    def get_live_inventory(self, data: dict[str, Any] | None = None) -> dict[str, list[dict[str, str]]]:
        """Return the current unfiltered resources for the options flow."""
        source = data or self._inventory_source or self.data
        return self.build_inventory(source)

    @staticmethod
    def build_inventory(data: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
        """Build option lists from normalized coordinator data."""
        inventory = {
            CONF_SELECTED_DISKS: [],
            CONF_SELECTED_FILESYSTEMS: [],
            CONF_SELECTED_SERVICES: [],
            CONF_SELECTED_NETWORK_INTERFACES: [],
            CONF_SELECTED_RAIDS: [],
            CONF_SELECTED_ZFS_POOLS: [],
            CONF_SELECTED_COMPOSE_PROJECTS: [],
            CONF_SELECTED_CONTAINERS: [],
        }

        for disk in data.get("disk", []):
            if not isinstance(disk, dict):
                continue
            value = str(disk.get("disk_key") or disk.get("devicename") or "")
            if not value:
                continue
            model = str(disk.get("model") or "").strip()
            label = value if not model or model == "unknown" else f"{value} ({model})"
            inventory[CONF_SELECTED_DISKS].append({"value": value, "label": label})

        for filesystem in data.get("fs", []):
            if not isinstance(filesystem, dict):
                continue
            value = str(filesystem.get("uuid") or "")
            if not value:
                continue
            label = str(filesystem.get("label") or filesystem.get("mountdir") or filesystem.get("devicefile") or value)
            inventory[CONF_SELECTED_FILESYSTEMS].append({"value": value, "label": label})

        for service in data.get("service", []):
            if not isinstance(service, dict):
                continue
            value = str(service.get("name") or "")
            if not value:
                continue
            label = str(service.get("title") or value)
            inventory[CONF_SELECTED_SERVICES].append({"value": value, "label": label})

        for network in data.get("network", []):
            if not isinstance(network, dict):
                continue
            value = str(network.get("uuid") or network.get("devicename") or "")
            if not value:
                continue
            label = str(network.get("devicename") or value)
            inventory[CONF_SELECTED_NETWORK_INTERFACES].append({"value": value, "label": label})

        for raid in data.get("raid", []):
            if not isinstance(raid, dict):
                continue
            value = str(raid.get("device") or raid.get("name") or raid.get("md") or "")
            if not value:
                continue
            label = str(raid.get("devicefile") or value)
            inventory[CONF_SELECTED_RAIDS].append({"value": value, "label": label})

        for pool in data.get("zfs", []):
            if not isinstance(pool, dict):
                continue
            value = str(pool.get("name") or "")
            if not value:
                continue
            inventory[CONF_SELECTED_ZFS_POOLS].append({"value": value, "label": value})

        for project in data.get("compose_projects", []):
            if not isinstance(project, dict):
                continue
            value = str(project.get("project_key") or project.get("name") or "")
            if not value:
                continue
            try:
                total = int(float(project.get("container_total") or 0))
            except (TypeError, ValueError):
                total = 0
            label = value if total <= 0 else f"{value} ({total})"
            inventory[CONF_SELECTED_COMPOSE_PROJECTS].append({"value": value, "label": label})

        for container in data.get("compose", []):
            if not isinstance(container, dict):
                continue
            value = str(container.get("container_key") or container.get("name") or "")
            if not value:
                continue
            name = str(container.get("name") or value)
            image = str(container.get("image") or "").strip()
            project = str(container.get("project_name") or "").strip()
            label = name
            if project:
                label = f"{label} [{project}]"
            if image:
                label = f"{label} ({image})"
            inventory[CONF_SELECTED_CONTAINERS].append({"value": value, "label": label})

        for key, options in inventory.items():
            unique: dict[str, str] = {}
            for option in options:
                unique.setdefault(str(option["value"]), str(option["label"]))
            inventory[key] = [{"value": value, "label": unique[value]} for value in sorted(unique, key=str.casefold)]

        return inventory

    def filter_data_by_selection(self, data: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        """Filter runtime collections by the persisted selection options."""
        filtered = dict(data)
        filtered["hwinfo"] = data.get("hwinfo", {})

        selected_disks = self._selected_values(options, CONF_SELECTED_DISKS)
        if selected_disks is not None:
            filtered["disk"] = [
                disk
                for disk in data.get("disk", [])
                if isinstance(disk, dict)
                and str(disk.get("disk_key") or disk.get("devicename") or "") in selected_disks
            ]
        else:
            filtered["disk"] = list(data.get("disk", []))

        selected_filesystems = self._selected_values(options, CONF_SELECTED_FILESYSTEMS)
        filesystems = list(data.get("fs", []))
        if selected_filesystems is not None:
            filesystems = [
                filesystem
                for filesystem in filesystems
                if isinstance(filesystem, dict) and str(filesystem.get("uuid") or "") in selected_filesystems
            ]
        filtered["fs"] = filesystems

        selected_services = self._selected_values(options, CONF_SELECTED_SERVICES)
        filtered["service"] = self._filter_collection(
            data.get("service", []),
            selected_services,
            lambda item: str(item.get("name") or ""),
        )

        selected_network = self._selected_values(options, CONF_SELECTED_NETWORK_INTERFACES)
        filtered["network"] = self._filter_collection(
            data.get("network", []),
            selected_network,
            lambda item: str(item.get("uuid") or item.get("devicename") or ""),
        )

        selected_raids = self._selected_values(options, CONF_SELECTED_RAIDS)
        filtered["raid"] = self._filter_collection(
            data.get("raid", []),
            selected_raids,
            lambda item: str(item.get("device") or item.get("name") or item.get("md") or ""),
        )

        selected_zfs = self._selected_values(options, CONF_SELECTED_ZFS_POOLS)
        filtered["zfs"] = self._filter_collection(
            data.get("zfs", []),
            selected_zfs,
            lambda item: str(item.get("name") or ""),
        )

        selected_projects = self._selected_values(options, CONF_SELECTED_COMPOSE_PROJECTS)
        selected_containers = self._selected_values(options, CONF_SELECTED_CONTAINERS)
        compose = self._filter_collection(
            data.get("compose", []),
            selected_containers,
            lambda item: str(item.get("container_key") or item.get("name") or ""),
        )
        if selected_projects is not None:
            compose = [
                container
                for container in compose
                if not str(container.get("project_key") or "")
                or str(container.get("project_key") or "") in selected_projects
            ]
        filtered["compose"] = compose

        compose_projects = self._filter_collection(
            data.get("compose_projects", []),
            selected_projects,
            lambda item: str(item.get("project_key") or item.get("name") or ""),
        )
        compose_project_counts = self._compose_project_counts(compose)
        filtered["compose_projects"] = [
            {
                **project,
                **compose_project_counts.get(
                    str(project.get("project_key") or project.get("name") or ""),
                    {
                        "container_total": 0,
                        "container_running": 0,
                        "container_not_running": 0,
                    },
                ),
            }
            for project in compose_projects
        ]

        filtered_container_keys = {
            str(container.get("container_key") or container.get("name") or "")
            for container in compose
            if isinstance(container, dict)
        }
        filtered_container_keys.discard("")
        filtered["compose_volumes"] = [
            volume
            for volume in data.get("compose_volumes", [])
            if isinstance(volume, dict) and str(volume.get("container_key") or "") in filtered_container_keys
        ]

        filtered["smart"] = list(data.get("smart", []))
        filtered["compose_summary"] = self._summarize_compose(
            compose,
            filtered["service"],
        )
        filtered["kvm"] = list(data.get("kvm", []))
        filtered["gpu"] = data.get("gpu", {})
        return filtered

    async def _async_get_hwinfo(self) -> dict[str, Any]:
        """Fetch and normalize OMV hardware information."""
        system_info = await self.api.async_call("System", "getInformation")
        hwinfo = self._normalize_hwinfo(system_info if isinstance(system_info, dict) else {})

        if not self.virtual_passthrough:
            cpu_temp = await self._fetch_optional("CpuTemp", "get")
            if isinstance(cpu_temp, dict) and cpu_temp.get("cputemp") is not None:
                hwinfo["cputemp"] = round(self._coerce_float(cpu_temp.get("cputemp")), 1)

        return hwinfo

    async def _async_get_smart(self, disks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch SMART information and merge relevant fields into disks."""
        method = "getListBg" if self.omv_version >= 7 else "getList"
        params = {"start": 0, "limit": 100}
        response = await self.api.async_call("Smart", method, params)

        if method == "getListBg" and not self._response_contains_records(response):
            _LOGGER.debug(
                "Smart.getListBg returned %s for OMV %s; falling back to Smart.getList",
                type(response).__name__,
                self.omv_version,
            )
            response = await self.api.async_call("Smart", "getList", params)

        smart_records = self._records_from_response(response)
        smart_by_key: dict[str, dict[str, Any]] = {}
        for record in smart_records:
            for key in self._disk_record_keys(record):
                smart_by_key[key] = record

        for disk in disks:
            smart_record = None
            for key in self._disk_record_keys(disk):
                smart_record = smart_by_key.get(key)
                if smart_record is not None:
                    break

            if smart_record is not None:
                if smart_record.get("temperature") not in (None, ""):
                    disk["temperature"] = self._coerce_optional_float(smart_record.get("temperature"))
                disk["overallstatus"] = str(smart_record.get("overallstatus", disk.get("overallstatus", "unknown")))
                disk["smart_details"] = {
                    key: value
                    for key, value in smart_record.items()
                    if key not in {"devicename", "devicefile", "canonicaldevicefile"}
                }

            canonical = str(disk.get("canonicaldevicefile") or "")
            if (
                not canonical
                or str(disk.get("devicename") or "").startswith(("mmcblk", "sr", "bcache"))
                or disk.get("hotpluggable")
            ):
                _LOGGER.debug(
                    "Skipping SMART attributes for %s (removable/hotpluggable)",
                    canonical or disk.get("devicename"),
                )
                continue

            attributes = await self._fetch_optional(
                "Smart",
                "getAttributes",
                {"devicefile": canonical},
            )
            smart_attributes: dict[str, Any] = {}
            for attribute in self._records_from_response(attributes):
                attrname = attribute.get("attrname")
                if attrname not in _SMART_ATTRIBUTE_NAMES:
                    continue
                raw_value = attribute.get("rawvalue", "unknown")
                if isinstance(raw_value, str) and " " in raw_value:
                    raw_value = raw_value.split(" ", 1)[0]
                disk[str(attrname)] = raw_value
                smart_attributes[str(attrname)] = raw_value
            if smart_attributes:
                disk["smart_attributes"] = smart_attributes

        return smart_records

    async def _async_get_gpu_info(self) -> dict[str, Any]:
        """Read Intel iGPU info from sysfs and apply spike filtering."""
        raw = await self.hass.async_add_executor_job(self._read_gpu_sysfs)
        if raw is None:
            return {}
        return self._apply_gpu_spike_filter(raw)

    def _read_gpu_sysfs(self) -> dict[str, Any] | None:
        """Read Intel GPU frequency from sysfs (blocking I/O, run in executor).

        Returns None when the sysfs paths are absent (no iGPU or HA is not
        running on the OMV host). Returns a dict with freq/load data otherwise.
        """
        try:
            cur_freq = int(Path(_GPU_CUR_FREQ_PATH).read_text().strip())
        except FileNotFoundError:
            _LOGGER.debug(
                "Intel GPU sysfs not present (%s) — GPU monitoring unavailable",
                _GPU_CUR_FREQ_PATH,
            )
            return None
        except (ValueError, OSError) as err:
            _LOGGER.debug("Could not read GPU current frequency: %s", err)
            return None

        max_freq: int | None = None
        try:
            max_freq = int(Path(_GPU_MAX_FREQ_PATH).read_text().strip())
        except (FileNotFoundError, ValueError, OSError) as err:
            _LOGGER.warning("Could not read GPU max frequency: %s", err)

        load_percent: float = 0.0
        if max_freq is not None and max_freq > 0:
            load_percent = round((cur_freq / max_freq) * 100, 1)

        return {
            "vendor": "intel",
            "model": "Intel Graphics",
            "cur_freq": cur_freq,
            "max_freq": max_freq,
            "load_percent": load_percent,
        }

    def _apply_gpu_spike_filter(self, gpu_raw: dict[str, Any]) -> dict[str, Any]:
        """Suppress single-cycle GPU load spikes; return the last stable reading.

        A non-zero load must persist for _GPU_CONFIRMATION_CYCLES consecutive
        updates before it is published, preventing momentary frequency bursts
        from causing the sensor value to flicker.  Zero-load readings always
        pass through immediately.
        """
        load = gpu_raw.get("load_percent") or 0.0
        if load > 0:
            self._gpu_load_counter += 1
        else:
            self._gpu_load_counter = 0

        if not self._last_stable_gpu or load == 0 or self._gpu_load_counter >= _GPU_CONFIRMATION_CYCLES:
            self._last_stable_gpu = gpu_raw
        else:
            _LOGGER.debug(
                "GPU load spike %.1f%% suppressed (counter=%d of %d needed)",
                load,
                self._gpu_load_counter,
                _GPU_CONFIRMATION_CYCLES,
            )

        return self._last_stable_gpu

    async def _fetch_or_empty(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Fetch data and return an empty collection on recoverable API failures."""
        try:
            return await self.api.async_call(service, method, params)
        except OMVApiError as err:
            _LOGGER.warning("Failed to fetch %s.%s: %s", service, method, err)
            return []

    async def _fetch_optional(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Fetch optional plugin data and suppress plugin-specific failures."""
        try:
            return await self.api.async_call(service, method, params)
        except (OMVApiError, OMVConnectionError) as err:
            _LOGGER.debug(
                "Optional RPC unavailable or failed: %s.%s params=%s error=%s",
                service,
                method,
                params,
                err,
            )
            return []

    async def _fetch_optional_background_json(
        self,
        service: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Resolve OMV background-task responses into parsed JSON payloads."""
        response = await self._fetch_optional(service, method, params)
        if response in (None, [], {}):
            return []
        if self._response_contains_records(response):
            return response

        filename = self._extract_background_filename(response)
        if not filename:
            # Handle inline output responses: OMV may return the command output
            # directly in an output/stdout field instead of a background task.
            inline_text = (
                self._extract_exec_output_text(response)
                if isinstance(response, dict)
                else (response if isinstance(response, str) else "")
            )
            if inline_text:
                parsed = self._parse_json_text(inline_text)
                if parsed is not None:
                    return parsed
            return response

        output = await self._async_read_exec_output(filename)
        if not output:
            return []

        parsed = self._parse_json_text(output)
        return parsed if parsed is not None else []

    async def _async_read_exec_output(self, filename: str) -> str:
        """Read one OMV background-task output file, polling briefly if needed."""
        output_text = ""
        for _attempt in range(6):
            response = await self._fetch_optional(
                "Exec",
                "getOutput",
                {"filename": filename, "pos": 0},
            )
            output_text = self._extract_exec_output_text(response)
            if output_text or not self._exec_output_is_running(response):
                return output_text
            await asyncio.sleep(0.2)
        return output_text

    def _extract_background_filename(self, response: Any) -> str:
        """Return an OMV background-task filename from a response payload."""
        if isinstance(response, str):
            stripped = response.strip()
            # JSON payloads start with { or [ - treat them as inline data, not paths.
            path_like = "/" in stripped or "." in stripped or "bgstatus" in stripped
            if stripped and not stripped.startswith(("{", "[")) and path_like:
                return self._sanitize_background_path(stripped)
            return ""

        if not isinstance(response, dict):
            return ""

        for key in ("filename", "file", "path", "bgstatusfile", "bgstatus"):
            value = response.get(key)
            if value not in (None, ""):
                return self._sanitize_background_path(str(value).strip())

        return ""

    @staticmethod
    def _sanitize_background_path(path: str) -> str:
        """Return the path if it is free of traversal sequences, otherwise empty string.

        Prevents a compromised or malicious OMV server from injecting paths
        like '../../etc/shadow' into Exec.getOutput calls (path traversal,
        OWASP A01/A03).
        """
        if ".." in PurePosixPath(path).parts:
            _LOGGER.warning(
                "Rejected suspicious background-task path %r (path traversal detected)",
                path,
            )
            return ""
        return path

    def _extract_exec_output_text(self, response: Any) -> str:
        """Extract the text payload from Exec.getOutput responses."""
        if isinstance(response, str):
            return response.strip()
        if not isinstance(response, dict):
            return ""

        for key in ("output", "stdout", "data", "text"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _exec_output_is_running(self, response: Any) -> bool:
        """Return whether Exec.getOutput indicates a still-running task."""
        if not isinstance(response, dict):
            return False
        for key in ("running", "isrunning", "inprogress"):
            if key in response:
                return self._coerce_bool(response.get(key))
        return False

    def _parse_json_text(self, value: str) -> Any:
        """Parse one JSON string and return None on failure.

        OMV background-task output (via Exec.getOutput) often prefixes the
        JSON payload with a shell command header such as
        ``export PATH=...; docker inspect <id> 2>&1``.
        We skip any leading non-JSON text by scanning for the first ``[`` or
        ``{`` character before attempting to parse.
        """
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

        # Try to find the start of the JSON payload after shell boilerplate.
        json_start = -1
        for marker in ("[", "{"):
            pos = value.find(marker)
            if pos != -1 and (json_start == -1 or pos < json_start):
                json_start = pos

        if json_start > 0:
            try:
                return json.loads(value[json_start:])
            except json.JSONDecodeError:
                pass

        _LOGGER.debug("Failed to parse OMV JSON output: %s", value[:200])
        return None

    def _normalize_hwinfo(self, info: dict[str, Any]) -> dict[str, Any]:
        """Normalize the OMV system information payload."""
        mem_total = int(self._coerce_float(info.get("memTotal")))
        # Use the API's memUsed (= total - available), which excludes reclaimable
        # kernel cache/buffers. This matches what the OMV GUI displays and avoids
        # reporting 90%+ on systems (e.g. Pi) where the kernel aggressively caches.
        mem_used = int(self._coerce_float(info.get("memUsed")))
        uptime_raw = info.get("uptime", 0)
        uptime_seconds = self._parse_uptime_seconds(uptime_raw)
        available_updates = int(self._coerce_float(info.get("availablePkgUpdates", 0)))
        load_average = info.get("loadAverage") if isinstance(info.get("loadAverage"), dict) else {}

        cpu_model = str(info.get("cpuModelName") or info.get("cpuModel") or info.get("cpu") or "unknown")
        kernel = str(info.get("kernel") or info.get("utsname") or "unknown")

        return {
            "hostname": str(info.get("hostname", "unknown")),
            "version": str(info.get("version", "unknown")),
            "cpuUtilization": round(self._coerce_float(info.get("cpuUtilization")), 1),
            "cputemp": round(self._coerce_float(info.get("cputemp")), 1),
            "memTotal": mem_total,
            "memUsed": mem_used,
            "memUsage": (round((mem_used / mem_total) * 100, 1) if mem_total else 0),
            "loadAverage": {
                "1min": self._coerce_float(load_average.get("1min")),
                "5min": self._coerce_float(load_average.get("5min")),
                "15min": self._coerce_float(load_average.get("15min")),
            },
            "uptime": uptime_raw,
            "uptimeEpoch": datetime.now(UTC) - timedelta(seconds=uptime_seconds),
            "configDirty": self._coerce_bool(info.get("configDirty")),
            "rebootRequired": self._coerce_bool(info.get("rebootRequired")),
            "availablePkgUpdates": available_updates,
            "pkgUpdatesAvailable": available_updates > 0,
            "cpuModel": cpu_model,
            "kernel": kernel,
        }

    def _normalize_filesystems(
        self,
        response: Any,
        disks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize filesystem data and map them to disks when possible."""
        filesystems: list[dict[str, Any]] = []
        for record in self._records_from_response(response):
            filesystem_type = str(record.get("type", ""))
            if filesystem_type in {"swap", "iso9660"}:
                continue

            identifier = str(record.get("uuid") or record.get("devicefile") or "")
            if not identifier:
                continue

            size_bytes = self._coerce_float(record.get("size"))
            available_bytes = self._coerce_float(record.get("available"))
            used_bytes = max(0.0, size_bytes - available_bytes)
            percentage = round(self._coerce_float(record.get("percentage")), 1)
            devicename = str(
                record.get("devicename")
                or record.get("devicefile")
                or record.get("canonicaldevicefile")
                or record.get("parentdevicefile")
                or identifier
            )
            if devicename.startswith("mapper/"):
                devicename = devicename[len("mapper/") :]

            filesystem = {
                "uuid": identifier,
                "label": str(record.get("label") or devicename),
                "type": filesystem_type or "unknown",
                "mounted": self._coerce_bool(record.get("mounted")),
                "devicename": devicename,
                "devicefile": str(record.get("devicefile") or ""),
                "canonicaldevicefile": str(record.get("canonicaldevicefile") or ""),
                "parentdevicefile": str(record.get("parentdevicefile") or ""),
                "mountdir": str(record.get("mountdir") or record.get("mountpoint") or record.get("dir") or ""),
                "size": round(size_bytes / 1000000000, 1),
                "available": round(available_bytes / 1000000000, 1),
                "used": round(used_bytes / 1000000000, 1),
                "percentage": percentage,
                "free_percentage": round(max(0.0, 100.0 - percentage), 1),
                "_readonly": self._coerce_bool(record.get("_readonly")),
                "_used": self._coerce_bool(record.get("_used")),
                "propreadonly": self._coerce_bool(record.get("propreadonly")),
            }
            filesystem["disk_key"] = self._map_filesystem_to_disk(filesystem, disks)
            filesystems.append(filesystem)

        return filesystems

    def _normalize_services(self, response: Any) -> list[dict[str, Any]]:
        """Normalize OMV service status data."""
        services: list[dict[str, Any]] = []
        for record in self._records_from_response(response):
            name = str(record.get("name") or "")
            if not name:
                continue
            services.append(
                {
                    "name": name,
                    "title": str(record.get("title") or name),
                    "enabled": self._coerce_bool(record.get("enabled")),
                    "running": self._coerce_bool(record.get("running")),
                }
            )

        return services

    def _normalize_network(self, response: Any) -> list[dict[str, Any]]:
        """Normalize network interfaces and calculate transfer rates."""
        network: list[dict[str, Any]] = []
        interval_seconds = max(int(self.update_interval.total_seconds()), 1)

        for record in self._records_from_response(response):
            if str(record.get("type") or "") == "loopback":
                continue

            uuid = str(record.get("uuid") or record.get("devicename") or "")
            if not uuid:
                continue

            stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
            current_rx = self._coerce_float(stats.get("rx_bytes", stats.get("rx_packets", 0)))
            current_tx = self._coerce_float(stats.get("tx_bytes", stats.get("tx_packets", 0)))
            previous = self._network_counters.get(uuid)

            if previous is None:
                rx_rate = 0.0
                tx_rate = 0.0
            else:
                rx_rate = round(
                    max(0.0, current_rx - previous["rx"]) * 8 / interval_seconds / 1_000_000,
                    2,
                )
                tx_rate = round(
                    max(0.0, current_tx - previous["tx"]) * 8 / interval_seconds / 1_000_000,
                    2,
                )

            self._network_counters[uuid] = {"rx": current_rx, "tx": current_tx}
            network.append(
                {
                    "uuid": uuid,
                    "devicename": str(record.get("devicename") or uuid),
                    "type": str(record.get("type") or "unknown"),
                    "method": str(record.get("method") or "unknown"),
                    "address": str(record.get("address") or ""),
                    "netmask": str(record.get("netmask") or ""),
                    "gateway": str(record.get("gateway") or ""),
                    "mtu": int(self._coerce_float(record.get("mtu"))),
                    "link": self._coerce_bool(record.get("link")),
                    "wol": self._coerce_bool(record.get("wol")),
                    "rx": rx_rate,
                    "tx": tx_rate,
                }
            )

        return network

    def _normalize_disks(self, response: Any) -> list[dict[str, Any]]:
        """Normalize disk inventory data."""
        disks: list[dict[str, Any]] = []
        for record in self._records_from_response(response):
            devicename = str(record.get("devicename") or "")
            if not devicename:
                continue
            total_size_gb = self._coerce_storage_gb(record.get("size"))
            disk = {
                "disk_key": devicename,
                "devicename": devicename,
                "canonicaldevicefile": str(record.get("canonicaldevicefile") or ""),
                "devicefile": str(record.get("devicefile") or ""),
                "size": record.get("size", "unknown"),
                "total_size_gb": total_size_gb or None,
                "vendor": str(record.get("vendor") or "unknown"),
                "model": str(record.get("model") or "unknown"),
                "description": str(record.get("description") or "unknown"),
                "raid_level": self._extract_raid_level(str(record.get("description") or "")),
                "serialnumber": str(record.get("serialnumber") or "unknown"),
                "israid": self._coerce_bool(record.get("israid")),
                "isroot": self._coerce_bool(record.get("isroot")),
                "isreadonly": self._coerce_bool(record.get("isreadonly")),
                "hotpluggable": self._coerce_bool(record.get("hotpluggable")),
                "temperature": self._coerce_optional_float(record.get("temperature")),
                "overallstatus": str(record.get("overallstatus") or "unknown"),
                "used_size_gb": None,
                "free_size_gb": None,
                "used_percentage": None,
                "free_percentage": None,
                "storage_source": None,
                "storage_label": None,
                "is_logical": False,
            }
            for attribute in _SMART_ATTRIBUTE_NAMES:
                disk[attribute] = record.get(attribute, "unknown")
            disks.append(disk)

        return disks

    def _augment_disks_with_logical_storage(
        self,
        disks: list[dict[str, Any]],
        filesystem_response: Any,
    ) -> list[dict[str, Any]]:
        """Add synthetic logical devices like md arrays when OMV omits them."""
        known_disk_keys = {
            str(disk.get("disk_key") or disk.get("devicename") or "") for disk in disks if isinstance(disk, dict)
        }

        for record in self._records_from_response(filesystem_response):
            logical_ref = self._find_logical_storage_reference(record)
            if not logical_ref or logical_ref in known_disk_keys:
                continue

            total_size_gb = self._coerce_storage_gb(record.get("size"))
            disk = {
                "disk_key": logical_ref,
                "devicename": logical_ref,
                "canonicaldevicefile": f"/dev/{logical_ref}",
                "devicefile": f"/dev/{logical_ref}",
                "size": self._format_storage_gb(total_size_gb),
                "total_size_gb": total_size_gb or None,
                "vendor": "OpenMediaVault",
                "model": "Linux MD RAID",
                "description": "Logical storage device",
                "raid_level": "unknown",
                "serialnumber": logical_ref,
                "israid": logical_ref.startswith("md"),
                "isroot": False,
                "isreadonly": False,
                "temperature": None,
                "overallstatus": "unknown",
                "used_size_gb": None,
                "free_size_gb": None,
                "used_percentage": None,
                "free_percentage": None,
                "storage_source": None,
                "storage_label": None,
                "is_logical": True,
            }
            for attribute in _SMART_ATTRIBUTE_NAMES:
                disk[attribute] = "unknown"
            disks.append(disk)
            known_disk_keys.add(logical_ref)

        return disks

    def _find_logical_storage_reference(self, record: dict[str, Any]) -> str | None:
        """Return a logical storage reference derived from filesystem payloads."""
        for value in (
            record.get("parentdevicefile"),
            record.get("canonicaldevicefile"),
            record.get("devicefile"),
            record.get("devicename"),
        ):
            normalized = self._normalize_device_reference(value)
            if self._is_logical_storage_device(normalized):
                return normalized
        return None

    def _is_logical_storage_device(self, value: str) -> bool:
        """Return whether a normalized device reference is a logical storage device."""
        return bool(value) and value.startswith("md")

    def _normalize_raids(self, disks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build RAID records from OMV disk inventory."""
        raids: list[dict[str, Any]] = []
        for disk in disks:
            if not isinstance(disk, dict) or not disk.get("israid"):
                continue
            disk_key = str(disk.get("disk_key") or disk.get("devicename") or "")
            if not disk_key:
                continue
            raids.append(
                {
                    "device": disk_key,
                    "devicefile": str(disk.get("canonicaldevicefile") or disk.get("devicefile") or f"/dev/{disk_key}"),
                    "disk_key": disk_key,
                    "state": "active",
                    "level": self._extract_raid_level(str(disk.get("description") or "")),
                    "health": self._raid_health_from_disk(disk),
                    "health_indicator": "",
                    "action_percent": None,
                }
            )
        return raids

    def _normalize_zfs_pools(
        self,
        response: Any,
        filesystems: list[dict[str, Any]],
        disks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize ZFS pools and map them to their owning storage device."""
        pools: list[dict[str, Any]] = []
        for record in self._records_from_response(response):
            name = str(record.get("name") or "")
            if not name:
                continue

            mountpoint = str(record.get("mountpoint") or "")
            size_gb = self._coerce_storage_gb(record.get("size"))
            alloc_gb = self._coerce_storage_gb(record.get("alloc"))
            free_gb = self._coerce_storage_gb(record.get("free") or record.get("available"))
            capacity = round(self._coerce_float(record.get("capacity")), 1)
            if not alloc_gb and size_gb and free_gb:
                alloc_gb = round(max(0.0, size_gb - free_gb), 1)
            if not free_gb and size_gb and alloc_gb:
                free_gb = round(max(0.0, size_gb - alloc_gb), 1)

            pool = dict(record)
            pool.update(
                {
                    "name": name,
                    "mountpoint": mountpoint,
                    "size": size_gb or record.get("size"),
                    "alloc": alloc_gb or record.get("alloc"),
                    "free": free_gb or record.get("free"),
                    "available": free_gb or self._coerce_storage_gb(record.get("available")),
                    "capacity": capacity or record.get("capacity"),
                    "disk_key": self._map_zfs_pool_to_disk(
                        mountpoint,
                        name,
                        filesystems,
                        disks,
                        record,
                    ),
                }
            )
            pools.append(pool)
        return pools

    def _normalize_named_collection(self, response: Any) -> list[dict[str, Any]]:
        """Normalize plugin collections that already contain flat object data."""
        return self._records_from_response(response)

    def _normalize_compose(self, response: Any) -> list[dict[str, Any]]:
        """Normalize Docker/Compose containers and extract project relationships."""
        containers: list[dict[str, Any]] = []
        for record in self._records_from_response(response):
            name = self._extract_container_name(record)
            if not name:
                continue

            container_id = self._extract_container_id(record)
            container_key = container_id or name
            project_name = self._extract_compose_project(record)
            service_name = self._extract_compose_service(record)

            container = {
                **record,
                "container_key": container_key,
                "container_id": container_id,
                "name": name,
                "image": self._extract_container_image(record),
                "version": self._extract_container_version(record),
                "state": self._extract_text_value(record, "state", "State"),
                "status_detail": self._extract_text_value(record, "status", "Status"),
                "created_at": self._coerce_datetime(
                    self._first_present_value(
                        record,
                        "created_at",
                        "createdAt",
                        "CreatedAt",
                        "created",
                        "Created",
                    )
                ),
                "started_at": self._coerce_datetime(
                    self._first_present_value(
                        record,
                        "started_at",
                        "startedAt",
                        "StartedAt",
                        "started",
                        "Started",
                    )
                ),
                "project_key": project_name,
                "project_name": project_name,
                "compose_service": service_name,
                "running": self._coerce_bool(record.get("running")) or self._is_container_running(record),
            }
            container["status"] = self._derive_container_project_status(container)
            containers.append(container)

        return containers

    def _compose_inspect_targets(
        self,
        compose: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the container subset that should receive inspect lookups."""
        selected_projects = self._selected_values(
            self.config_entry.options,
            CONF_SELECTED_COMPOSE_PROJECTS,
        )
        selected_containers = self._selected_values(
            self.config_entry.options,
            CONF_SELECTED_CONTAINERS,
        )

        targets = compose
        if selected_containers is not None:
            targets = [
                container
                for container in targets
                if str(container.get("container_key") or container.get("name") or "") in selected_containers
            ]
        if selected_projects is not None:
            targets = [
                container
                for container in targets
                if not str(container.get("project_key") or "")
                or str(container.get("project_key") or "") in selected_projects
            ]
        return targets

    async def _fetch_compose_inspect(
        self,
        compose: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Fetch docker inspect payloads for compose containers when available."""
        inspect_by_key: dict[str, dict[str, Any]] = {}
        for container in compose:
            if not isinstance(container, dict):
                continue
            container_id = str(
                container.get("container_id") or container.get("container_key") or container.get("name") or ""
            )
            container_key = str(container.get("container_key") or container.get("name") or "")
            if not container_id or not container_key:
                continue

            response = await self._fetch_optional_background_json(
                "Compose",
                "doContainerCommand",
                {"id": container_id, "command": "inspect", "command2": ""},
            )
            inspect = self._normalize_compose_inspect_response(response)
            if inspect:
                inspect_by_key[container_key] = inspect

        return inspect_by_key

    def _normalize_compose_inspect_response(self, response: Any) -> dict[str, Any] | None:
        """Return the first docker inspect record from a parsed OMV response."""
        if isinstance(response, list):
            first = next((item for item in response if isinstance(item, dict)), None)
            return first

        records = self._records_from_response(response)
        if records:
            return records[0]

        if isinstance(response, dict):
            return response

        return None

    def _merge_compose_inspect(
        self,
        compose: list[dict[str, Any]],
        inspect_by_key: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge inspect metadata into normalized compose containers."""
        merged: list[dict[str, Any]] = []
        for container in compose:
            if not isinstance(container, dict):
                continue
            container_key = str(container.get("container_key") or container.get("name") or "")
            inspect = inspect_by_key.get(container_key)
            if inspect is None:
                merged.append(container)
                continue

            merged_container = {**container, **inspect}
            merged_container["project_key"] = str(container.get("project_key") or "") or self._extract_compose_project(
                merged_container
            )
            merged_container["project_name"] = str(container.get("project_name") or "") or str(
                merged_container.get("project_key") or ""
            )
            merged_container["compose_service"] = str(
                container.get("compose_service") or ""
            ) or self._extract_compose_service(merged_container)
            merged_container["version"] = self._extract_container_version(merged_container)
            merged_container["status"] = self._derive_container_project_status(merged_container)
            merged.append(merged_container)

        return merged

    def _normalize_compose_projects(
        self,
        response: Any,
        compose: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize compose file/project records and merge runtime container counts."""
        compose_counts = self._compose_project_counts(compose)
        projects: dict[str, dict[str, Any]] = {}

        for record in self._records_from_response(response):
            project_name = self._extract_text_value(record, "name", "Name")
            if not project_name:
                continue

            projects[project_name] = {
                **record,
                "project_key": project_name,
                "name": project_name,
                "uuid": self._extract_text_value(record, "uuid", "UUID"),
                "status": self._extract_text_value(record, "status", "Status"),
                "uptime": self._extract_text_value(record, "uptime", "Uptime"),
                "service_name": self._extract_text_value(
                    record,
                    "svcname",
                    "service",
                    "service_name",
                    "serviceName",
                ),
                "image": self._extract_text_value(record, "image", "imgname", "Image"),
                "description": self._extract_text_value(
                    record,
                    "description",
                    "Description",
                ),
                "ports": self._extract_text_value(record, "ports", "Ports"),
                "container_total": 0,
                "container_running": 0,
                "container_not_running": 0,
            }

        for project_name, counts in compose_counts.items():
            project = projects.setdefault(
                project_name,
                {
                    "project_key": project_name,
                    "name": project_name,
                    "uuid": "",
                    "status": "",
                    "uptime": "",
                    "service_name": "",
                    "image": "",
                    "description": "",
                    "ports": "",
                    "container_total": 0,
                    "container_running": 0,
                    "container_not_running": 0,
                },
            )
            project.update(counts)

        return [projects[project_name] for project_name in sorted(projects, key=str.casefold)]

    def _link_compose_projects(
        self,
        compose: list[dict[str, Any]],
        compose_projects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Attach normalized compose project metadata to container records."""
        project_by_key = {
            str(project.get("project_key") or ""): project
            for project in compose_projects
            if isinstance(project, dict) and str(project.get("project_key") or "")
        }
        linked: list[dict[str, Any]] = []

        for container in compose:
            project_key = str(container.get("project_key") or "")
            project = project_by_key.get(project_key)
            if project is None:
                project = self._match_compose_project(container, compose_projects)
                if project is not None:
                    project_key = str(project.get("project_key") or "")

            if project is None:
                linked.append(container)
                continue

            project_status = self._extract_text_value(project, "status")
            project_name = str(project.get("name") or project_key)
            linked.append(
                {
                    **container,
                    "project_key": project_key,
                    "project_name": project_name,
                    "project_uuid": self._extract_text_value(project, "uuid"),
                    "project_status": project_status or self._derive_container_project_status(container),
                    "project_uptime": self._extract_text_value(project, "uptime"),
                }
            )

        return linked

    def _normalize_compose_volumes(
        self,
        response: Any,
        compose: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize compose volumes from the native OMV endpoint with mount fallback."""
        fallback_volumes = self._normalize_compose_mount_volumes(compose)
        fallback_by_key = {
            str(volume.get("volume_key") or ""): volume
            for volume in fallback_volumes
            if isinstance(volume, dict) and str(volume.get("volume_key") or "")
        }

        native_records = self._records_from_response(response)
        if not native_records:
            return fallback_volumes

        containers_by_volume = self._compose_containers_by_volume(compose)
        volumes: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for record in native_records:
            volume_name = self._extract_text_value(record, "name", "Name")
            if not volume_name:
                continue

            container = containers_by_volume.get(volume_name, {})
            merged = self._build_compose_volume_record(volume_name, record, container)
            if not merged:
                continue

            fallback = fallback_by_key.get(str(merged.get("volume_key") or ""))
            if fallback is not None:
                merged = self._merge_compose_volume_record(merged, fallback)

            seen_keys.add(str(merged.get("volume_key") or ""))
            volumes.append(merged)

        for fallback in fallback_volumes:
            volume_key = str(fallback.get("volume_key") or "")
            if volume_key and volume_key not in seen_keys:
                volumes.append(fallback)

        return volumes

    def _normalize_compose_mount_volumes(
        self,
        compose: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize container mount data into fallback volume records."""
        volumes: list[dict[str, Any]] = []

        for container in compose:
            if not isinstance(container, dict):
                continue

            raw_mounts = self._first_present_value(container, "mounts", "Mounts")
            mounts = self._iter_container_mounts(raw_mounts)
            if not mounts:
                continue

            for index, mount in enumerate(mounts, start=1):
                if isinstance(mount, dict):
                    mount_type = self._extract_text_value(mount, "Type", "type").lower()
                    if mount_type and mount_type != "volume":
                        continue
                    volume_name = self._extract_volume_name(mount, index)
                    volume = self._build_compose_volume_record(volume_name, mount, container)
                elif isinstance(mount, str):
                    # OMV7: plain named-volume string — no size data available
                    volume_name = self._extract_volume_name(mount, index)
                    if not volume_name:
                        continue
                    volume = self._build_compose_volume_record(volume_name, {}, container)
                else:
                    continue
                if volume:
                    volumes.append(volume)

        return volumes

    def _compose_containers_by_volume(
        self,
        compose: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Map volume names to the container that mounts them."""
        containers_by_volume: dict[str, dict[str, Any]] = {}
        for container in compose:
            if not isinstance(container, dict):
                continue
            mounts = self._iter_container_mounts(
                self._first_present_value(container, "Mounts", "mounts"),
            )
            for index, mount in enumerate(mounts, start=1):
                if isinstance(mount, dict):
                    mount_type = self._extract_text_value(mount, "Type", "type").lower()
                    if mount_type and mount_type != "volume":
                        continue
                elif not isinstance(mount, str):
                    continue
                volume_name = self._extract_volume_name(mount, index)
                if volume_name:
                    containers_by_volume.setdefault(volume_name, container)
        return containers_by_volume

    def _build_compose_volume_record(
        self,
        volume_name: str,
        record: dict[str, Any],
        container: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build one normalized compose volume record."""
        container_key = str(container.get("container_key") or container.get("name") or "")
        if not container_key:
            return None

        return {
            "volume_key": f"{container_key}:{volume_name}",
            "display_name": volume_name,
            "name": volume_name,
            "size_gb": self._extract_volume_size_gb(record),
            "source": self._extract_volume_source(record),
            "destination": self._extract_volume_destination(record),
            "mountpoint": self._extract_text_value(record, "mountpoint", "Mountpoint"),
            "driver": self._extract_text_value(record, "driver", "Driver"),
            "container_key": container_key,
            "container_name": str(container.get("name") or container_key),
            "project_key": str(container.get("project_key") or ""),
            "project_name": str(container.get("project_name") or ""),
            "image": str(container.get("image") or ""),
            "version": str(container.get("version") or ""),
        }

    def _merge_compose_volume_record(
        self,
        primary: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge a native OMV volume record with fallback mount metadata."""
        merged = dict(fallback)
        for key, value in primary.items():
            if value not in (None, ""):
                merged[key] = value
        return merged

    def _summarize_compose_projects(
        self,
        compose: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build Compose project summaries from normalized container data."""
        projects = self._compose_project_counts(compose)
        return [
            {
                "project_key": project_key,
                "name": project_key,
                **counts,
            }
            for project_key, counts in sorted(projects.items(), key=lambda item: item[0].casefold())
        ]

    def _compose_project_counts(
        self,
        compose: list[dict[str, Any]],
    ) -> dict[str, dict[str, int]]:
        """Return per-project container counts from normalized containers."""
        counts: dict[str, dict[str, int]] = {}
        for container in compose:
            if not isinstance(container, dict):
                continue
            project_key = str(container.get("project_key") or "")
            if not project_key:
                continue
            project_counts = counts.setdefault(
                project_key,
                {
                    "container_total": 0,
                    "container_running": 0,
                    "container_not_running": 0,
                },
            )
            project_counts["container_total"] += 1
            if self._is_container_running(container):
                project_counts["container_running"] += 1

        for project_counts in counts.values():
            project_counts["container_not_running"] = max(
                0,
                project_counts["container_total"] - project_counts["container_running"],
            )

        return counts

    def _summarize_compose(
        self,
        compose: list[dict[str, Any]],
        services: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Summarize Docker/Compose container counts when the service is available."""
        if not self._has_container_service(services):
            return {}

        total = 0
        running = 0
        for container in compose:
            if not isinstance(container, dict):
                continue
            total += 1
            if self._is_container_running(container):
                running += 1

        return {
            "total": total,
            "running": running,
            "not_running": max(0, total - running),
        }

    def _derive_container_project_status(self, container: dict[str, Any]) -> str:
        """Return a stable status value for a container or its parent compose file."""
        state = str(container.get("state") or "").strip().lower()
        if state in _CONTAINER_STATE_TO_PROJECT_STATUS:
            return _CONTAINER_STATE_TO_PROJECT_STATUS[state]

        status_detail = str(container.get("status_detail") or "").strip().lower()
        if status_detail.startswith("up"):
            return "UP"
        if status_detail.startswith("exited") or status_detail.startswith("stopped"):
            return "STOPPED"
        if status_detail.startswith("created") or status_detail.startswith("dead"):
            return "DOWN"
        return str(container.get("status") or "")

    def _match_compose_project(
        self,
        container: dict[str, Any],
        compose_projects: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Infer a compose project for OMV payloads that omit project labels."""
        container_name = str(container.get("name") or "")
        compose_service = str(container.get("compose_service") or "")

        for project in compose_projects:
            project_name = str(project.get("project_key") or project.get("name") or "")
            if not project_name:
                continue

            prefixes = [f"{project_name}-", f"{project_name}_"]
            if compose_service:
                prefixes.extend(
                    [
                        f"{project_name}-{compose_service}-",
                        f"{project_name}_{compose_service}_",
                    ]
                )

            if container_name == project_name or any(container_name.startswith(prefix) for prefix in prefixes):
                return project

        return None

    def _extract_container_name(self, record: dict[str, Any]) -> str:
        """Return the most useful container name from a compose payload."""
        value = self._first_present_value(record, "name", "Name", "Names")
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, str):
            value = value.split(",", 1)[0].strip().lstrip("/")
            return value
        return ""

    def _extract_container_id(self, record: dict[str, Any]) -> str:
        """Return a stable container identifier when the payload provides one."""
        return self._extract_text_value(
            record,
            "container_id",
            "containerId",
            "id",
            "Id",
            "ID",
        )

    def _extract_container_image(self, record: dict[str, Any]) -> str:
        """Return the normalized image reference for a container."""
        return self._extract_text_value(
            record,
            "image",
            "Image",
            "image_name",
        ) or self._extract_text_value(
            self._nested_record_value(record, "Config") or {},
            "Image",
            "image",
        )

    def _extract_container_version(self, record: dict[str, Any]) -> str:
        """Return the best available version for one container image."""
        metadata_version = self._extract_metadata_value(
            record,
            "org.opencontainers.image.version",
        )
        if metadata_version:
            return metadata_version

        return self._extract_container_image_tag(record)

    def _extract_container_image_tag(self, record: dict[str, Any]) -> str:
        """Return the image tag as a fallback version hint."""
        image = self._extract_container_image(record)
        if not image:
            return ""

        image_without_digest = image.split("@", 1)[0]
        last_segment = image_without_digest.rsplit("/", 1)[-1]
        if ":" not in last_segment:
            return ""

        return last_segment.rsplit(":", 1)[-1].strip()

    def _extract_compose_project(self, record: dict[str, Any]) -> str:
        """Return the compose project name if OMV exposes one explicitly."""
        project = self._extract_text_value(
            record,
            "project",
            "project_name",
            "projectName",
            "compose_project",
            "composeProject",
        )
        if project:
            return project

        labels = record.get("labels") or record.get("Labels")
        if isinstance(labels, dict):
            return str(labels.get("com.docker.compose.project") or "").strip()
        if isinstance(labels, list):
            for label in labels:
                if not isinstance(label, str):
                    continue
                if label.startswith("com.docker.compose.project="):
                    return label.split("=", 1)[1].strip()
        return ""

    def _extract_compose_service(self, record: dict[str, Any]) -> str:
        """Return the compose service name if OMV exposes one explicitly."""
        service = self._extract_text_value(
            record,
            "service",
            "service_name",
            "serviceName",
            "compose_service",
            "composeService",
        )
        if service:
            return service

        labels = record.get("labels") or record.get("Labels")
        if isinstance(labels, dict):
            return str(labels.get("com.docker.compose.service") or "").strip()
        if isinstance(labels, list):
            for label in labels:
                if not isinstance(label, str):
                    continue
                if label.startswith("com.docker.compose.service="):
                    return label.split("=", 1)[1].strip()
        return ""

    def _first_present_value(self, record: dict[str, Any], *keys: str) -> Any:
        """Return the first present record value for a list of possible keys."""
        for key in keys:
            if key in record and record[key] not in (None, ""):
                return record[key]
        return None

    def _extract_text_value(self, record: dict[str, Any], *keys: str) -> str:
        """Return the first non-empty textual value for a list of keys."""
        value = self._first_present_value(record, *keys)
        if value in (None, ""):
            return ""
        return str(value).strip()

    def _extract_metadata_value(self, record: dict[str, Any], metadata_key: str) -> str:
        """Return one metadata value from labels or annotations."""
        candidates = (
            ("labels",),
            ("Labels",),
            ("annotations",),
            ("Annotations",),
            ("config", "labels"),
            ("config", "Labels"),
            ("config", "annotations"),
            ("config", "Annotations"),
            ("Config", "labels"),
            ("Config", "Labels"),
            ("Config", "annotations"),
            ("Config", "Annotations"),
            # docker inspect ImageManifestDescriptor.annotations (nginx, official images)
            ("ImageManifestDescriptor", "annotations"),
            ("imageManifestDescriptor", "annotations"),
            ("imageManifestDescriptor", "Annotations"),
        )
        for path in candidates:
            extracted = self._extract_kv_collection_value(
                self._nested_record_value(record, *path),
                metadata_key,
            )
            if extracted:
                return extracted
        return ""

    def _nested_record_value(self, record: dict[str, Any], *path: str) -> Any:
        """Return a nested value from a record when every path segment is present."""
        value: Any = record
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        return value

    def _extract_kv_collection_value(self, value: Any, key: str) -> str:
        """Return a key from dict/list based metadata collections."""
        if isinstance(value, dict):
            extracted = value.get(key)
            return str(extracted).strip() if extracted not in (None, "") else ""

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    extracted = item.get(key)
                    if extracted not in (None, ""):
                        return str(extracted).strip()
                elif isinstance(item, str) and item.startswith(f"{key}="):
                    return item.split("=", 1)[1].strip()

        if isinstance(value, str) and value.startswith(f"{key}="):
            return value.split("=", 1)[1].strip()

        return ""

    def _iter_container_mounts(self, mounts: Any) -> list[Any]:
        """Return normalized mount items from OMV compose payloads."""
        if isinstance(mounts, list):
            return [item for item in mounts if item not in (None, "")]

        if isinstance(mounts, dict):
            return [mounts]

        if isinstance(mounts, str):
            normalized = mounts.replace("<br/>", "\n")
            return [part.strip() for part in re.split(r"[,\n]", normalized) if part.strip()]

        return []

    def _extract_volume_name(self, mount: Any, index: int) -> str:
        """Return a human readable volume name for one mount record."""
        if isinstance(mount, dict):
            for key in ("name", "Name", "source", "Source"):
                value = mount.get(key)
                if value not in (None, ""):
                    text = str(value).strip()
                    if text:
                        return PurePosixPath(text).name or text
            return f"volume_{index}"

        if isinstance(mount, str):
            text = mount.strip()
            # Absolute paths are bind mounts, not named volumes — skip them
            if text and not text.startswith("/"):
                return PurePosixPath(text).name or text

        return ""

    def _extract_volume_source(self, mount: Any) -> str:
        """Return the volume source value when available."""
        if isinstance(mount, dict):
            return self._extract_text_value(
                mount,
                "source",
                "Source",
                "name",
                "Name",
                "mountpoint",
                "Mountpoint",
            )
        if isinstance(mount, str):
            return mount.strip()
        return ""

    def _extract_volume_destination(self, mount: Any) -> str:
        """Return the mount destination when available."""
        if isinstance(mount, dict):
            return self._extract_text_value(
                mount,
                "destination",
                "Destination",
                "target",
                "Target",
            )
        return ""

    def _extract_volume_size_gb(self, mount: Any) -> float | None:
        """Return the mount size in decimal gigabytes when available."""
        if not isinstance(mount, dict):
            return None

        for key in ("size_gb", "sizeGb", "SizeGb"):
            value = mount.get(key)
            if value not in (None, ""):
                return round(self._coerce_float(value), 1)

        for key in (
            "size",
            "Size",
            "usage",
            "Usage",
            "SizeRw",
            "size_rw",
            "Data",
            "data",
        ):
            value = mount.get(key)
            if value not in (None, ""):
                raw = self._coerce_float(value)
                if raw <= 0:
                    return None
                # Raw integer/float byte counts: compute with enough precision
                # to avoid rounding tiny volumes (< 50 MB) to 0.0 GB.
                if isinstance(value, int | float):
                    return round(raw / 1_000_000_000, 4)
                size_gb = self._coerce_storage_gb(value)
                return size_gb if size_gb > 0 else None

        return None

    def _has_container_service(self, services: list[dict[str, Any]]) -> bool:
        """Return whether Docker/Compose is present as a service."""
        for service in services:
            if not isinstance(service, dict):
                continue
            name = str(service.get("name") or "").strip().lower()
            title = str(service.get("title") or "").strip().lower()
            if name in {"compose", "docker"} or "docker" in title or "compose" in title:
                return True
        return False

    def _is_container_running(self, container: dict[str, Any]) -> bool:
        """Return whether a compose container is currently running."""
        if container.get("running") is True:
            return True

        state = str(container.get("state") or "").strip().lower()
        if state in {"running", "healthy"}:
            return True
        if state.startswith("running "):
            return True

        status = str(container.get("status_detail") or container.get("status") or "").strip().lower()
        if status in {"running", "up", "healthy"}:
            return True
        return status.startswith("up ") or status.startswith("running ")

    def _apply_storage_metrics(
        self,
        disks: list[dict[str, Any]],
        filesystems: list[dict[str, Any]],
        zfs_pools: list[dict[str, Any]],
    ) -> None:
        """Project the most relevant logical storage metrics onto each disk-like device."""
        best_by_disk: dict[str, dict[str, Any]] = {}

        for filesystem in filesystems:
            disk_key = str(filesystem.get("disk_key") or "")
            if not disk_key:
                continue
            candidate = {
                "storage_source": "filesystem",
                "storage_label": filesystem.get("label") or filesystem.get("mountdir") or filesystem.get("uuid"),
                "total_size_gb": filesystem.get("size"),
                "used_size_gb": filesystem.get("used"),
                "free_size_gb": filesystem.get("available"),
                "used_percentage": filesystem.get("percentage"),
                "free_percentage": filesystem.get("free_percentage"),
            }
            self._set_best_storage_candidate(best_by_disk, disk_key, candidate)

        for pool in zfs_pools:
            disk_key = str(pool.get("disk_key") or "")
            if not disk_key:
                continue
            total_size_gb = self._coerce_float(pool.get("size"))
            used_size_gb = self._coerce_float(pool.get("alloc"))
            free_size_gb = self._coerce_float(pool.get("free") or pool.get("available"))
            if not used_size_gb and total_size_gb and free_size_gb:
                used_size_gb = round(max(0.0, total_size_gb - free_size_gb), 1)
            if not free_size_gb and total_size_gb and used_size_gb:
                free_size_gb = round(max(0.0, total_size_gb - used_size_gb), 1)
            candidate = {
                "storage_source": "zfs",
                "storage_label": pool.get("name"),
                "total_size_gb": total_size_gb or None,
                "used_size_gb": used_size_gb or None,
                "free_size_gb": free_size_gb or None,
                "used_percentage": self._coerce_float(pool.get("capacity")) or None,
                "free_percentage": round(max(0.0, 100.0 - self._coerce_float(pool.get("capacity"))), 1)
                if self._coerce_float(pool.get("capacity"))
                else None,
            }
            self._set_best_storage_candidate(best_by_disk, disk_key, candidate, prefer=True)

        for disk in disks:
            disk_key = str(disk.get("disk_key") or "")
            candidate = best_by_disk.get(disk_key)
            if candidate is None:
                continue
            disk.update(candidate)

    def _set_best_storage_candidate(
        self,
        best_by_disk: dict[str, dict[str, Any]],
        disk_key: str,
        candidate: dict[str, Any],
        *,
        prefer: bool = False,
    ) -> None:
        """Keep the most useful storage candidate for a disk."""
        current = best_by_disk.get(disk_key)
        if current is None:
            best_by_disk[disk_key] = candidate
            return
        candidate_size = self._coerce_float(candidate.get("total_size_gb"))
        current_size = self._coerce_float(current.get("total_size_gb"))
        if prefer or candidate_size > current_size:
            best_by_disk[disk_key] = candidate

    def _map_zfs_pool_to_disk(
        self,
        mountpoint: str,
        name: str,
        filesystems: list[dict[str, Any]],
        disks: list[dict[str, Any]],
        record: dict[str, Any],
    ) -> str | None:
        """Map a ZFS pool to the same owner as its mounted filesystem."""
        normalized_mountpoint = mountpoint.rstrip("/") if mountpoint else ""
        mountpoint_name = PurePosixPath(normalized_mountpoint).name if normalized_mountpoint else ""
        for filesystem in filesystems:
            if not isinstance(filesystem, dict):
                continue
            filesystem_mountdir = str(filesystem.get("mountdir") or "")
            normalized_filesystem_mountdir = filesystem_mountdir.rstrip("/")
            filesystem_mount_name = (
                PurePosixPath(normalized_filesystem_mountdir).name if normalized_filesystem_mountdir else ""
            )

            if (
                normalized_mountpoint
                and normalized_filesystem_mountdir
                and (
                    normalized_filesystem_mountdir == normalized_mountpoint
                    or normalized_filesystem_mountdir.startswith(f"{normalized_mountpoint}/")
                    or normalized_mountpoint.startswith(f"{normalized_filesystem_mountdir}/")
                )
            ):
                return str(filesystem.get("disk_key") or "") or None
            if name and str(filesystem.get("label") or "") == name:
                return str(filesystem.get("disk_key") or "") or None
            if name and filesystem_mount_name == name:
                return str(filesystem.get("disk_key") or "") or None
            if mountpoint_name and filesystem_mount_name == mountpoint_name:
                return str(filesystem.get("disk_key") or "") or None

        for field in ("id", "origin"):
            normalized = self._normalize_device_reference(record.get(field))
            if not normalized:
                continue
            for disk in disks:
                if normalized in self._disk_record_keys(disk):
                    return str(disk.get("disk_key") or disk.get("devicename") or "")

        return self._match_disk_by_size(record.get("size"), disks)

    def _map_filesystem_to_disk(
        self,
        filesystem: dict[str, Any],
        disks: list[dict[str, Any]],
    ) -> str | None:
        """Map a filesystem to its parent disk using the available device paths."""
        candidates = {
            self._normalize_device_reference(filesystem.get("devicename")),
            self._normalize_device_reference(filesystem.get("devicefile")),
            self._normalize_device_reference(filesystem.get("canonicaldevicefile")),
            self._normalize_device_reference(filesystem.get("parentdevicefile")),
        }
        candidates.discard("")

        for disk in disks:
            disk_keys = self._disk_record_keys(disk)
            if candidates & disk_keys:
                return str(disk.get("disk_key") or disk.get("devicename") or "")

        for candidate in list(candidates):
            parent_candidate = re.sub(r"p?\d+$", "", candidate)
            if parent_candidate == candidate:
                continue
            for disk in disks:
                if parent_candidate in self._disk_record_keys(disk):
                    return str(disk.get("disk_key") or disk.get("devicename") or "")

        return self._match_disk_by_size(filesystem.get("size"), disks)

    def _match_disk_by_size(
        self,
        observed_size_gb: Any,
        disks: list[dict[str, Any]],
    ) -> str | None:
        """Match a storage item to a unique disk by approximate size."""
        size_gb = self._coerce_float(observed_size_gb)
        if size_gb <= 0:
            return None

        exact_candidates = {
            str(disk.get("disk_key") or disk.get("devicename") or "")
            for disk in disks
            if abs(self._coerce_float(disk.get("total_size_gb")) - size_gb) <= 1.0
        }
        exact_candidates.discard("")
        if len(exact_candidates) == 1:
            return next(iter(exact_candidates))

        approximate_candidates = {
            str(disk.get("disk_key") or disk.get("devicename") or "")
            for disk in disks
            if self._coerce_float(disk.get("total_size_gb")) >= size_gb
            and self._coerce_float(disk.get("total_size_gb")) > 0
            and (self._coerce_float(disk.get("total_size_gb")) - size_gb)
            / self._coerce_float(disk.get("total_size_gb"))
            <= 0.08
        }
        approximate_candidates.discard("")
        if len(approximate_candidates) == 1:
            return next(iter(approximate_candidates))

        return None

    def _selected_values(
        self,
        options: dict[str, Any],
        key: str,
    ) -> set[str] | None:
        """Return the selected values or None when the option was never set."""
        if key not in options:
            return None
        return {str(value) for value in options.get(key, [])}

    def _filter_collection(
        self,
        collection: Any,
        selected: set[str] | None,
        key_fn,
    ) -> list[dict[str, Any]]:
        """Filter a normalized collection using its selection set."""
        items = [item for item in collection if isinstance(item, dict)] if isinstance(collection, list) else []
        if selected is None:
            return items
        return [item for item in items if key_fn(item) in selected]

    def _disk_record_keys(self, record: dict[str, Any]) -> set[str]:
        """Return all lookup keys that can identify a disk record."""
        keys = {
            self._normalize_device_reference(record.get("disk_key")),
            self._normalize_device_reference(record.get("devicename")),
            self._normalize_device_reference(record.get("devicefile")),
            self._normalize_device_reference(record.get("canonicaldevicefile")),
        }
        keys.discard("")
        return keys

    def _normalize_device_reference(self, value: Any) -> str:
        """Normalize device references for matching between disks and filesystems."""
        if value in (None, ""):
            return ""
        text = str(value).strip()
        if text.startswith("/dev/"):
            text = text[5:]
        if text.startswith("mapper/"):
            text = text[len("mapper/") :]
        return text

    def _extract_raid_level(self, description: str) -> str:
        """Extract a RAID level hint from the disk description."""
        match = re.search(r"raid\s*([0-9]+)", description, re.IGNORECASE)
        if match:
            return f"raid{match.group(1)}"
        return "unknown"

    def _raid_health_from_disk(self, disk: dict[str, Any]) -> str:
        """Derive a coarse RAID health state from the disk record."""
        status = str(disk.get("overallstatus") or "").upper()
        if status in {"PASSED", "GOOD", "ONLINE"}:
            return "clean"
        if status and status != "UNKNOWN":
            return status.lower()
        if disk.get("israid") or disk.get("is_logical"):
            return "clean"
        return "unknown"

    def _coerce_storage_gb(self, value: Any) -> float:
        """Best-effort conversion of storage values to decimal gigabytes."""
        if value in (None, ""):
            return 0.0
        if isinstance(value, int | float):
            return round(float(value) / 1000000000, 1)
        if isinstance(value, str):
            match = re.search(r"(-?\d+(?:[\.,]\d+)?)\s*([kmgtpezy]?i?b)?", value.strip(), re.IGNORECASE)
            if not match:
                return 0.0
            number = float(match.group(1).replace(",", "."))
            unit = (match.group(2) or "b").lower()
            factors = {
                "b": 1 / 1000000000,
                "kb": 1 / 1000000,
                "mb": 1 / 1000,
                "gb": 1,
                "tb": 1000,
                "pb": 1000000,
                "kib": 1024 / 1000000000,
                "mib": 1048576 / 1000000000,
                "gib": 1073741824 / 1000000000,
                "tib": 1099511627776 / 1000000000,
            }
            return round(number * factors.get(unit, 1), 1)
        return 0.0

    def _format_storage_gb(self, value: float) -> str:
        """Format decimal gigabytes for device metadata."""
        if value <= 0:
            return "unknown"
        return f"{value:.1f} GB"

    def _response_contains_records(self, response: Any) -> bool:
        """Return whether an OMV response already contains record objects."""
        if isinstance(response, list):
            return any(isinstance(item, dict) for item in response)
        if isinstance(response, dict):
            data = response.get("data")
            return isinstance(data, list)
        return False

    def _records_from_response(self, response: Any) -> list[dict[str, Any]]:
        """Extract record lists from OMV responses."""
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if isinstance(response, dict):
            if isinstance(response.get("data"), list):
                return [item for item in response["data"] if isinstance(item, dict)]
            return [response]
        return []

    def _parse_uptime_seconds(self, value: Any) -> int:
        """Convert OMV uptime values into seconds."""
        if isinstance(value, int | float):
            return max(0, int(value))

        if isinstance(value, str):
            stripped = value.strip()
            try:
                return max(0, int(float(stripped)))
            except ValueError:
                match = re.match(
                    r"(?P<days>\d+)\s+days\s+(?P<hours>\d+)\s+hours\s+(?P<minutes>\d+)\s+minutes\s+(?P<seconds>\d+)\s+seconds",
                    stripped,
                )
                if match:
                    return (
                        int(match.group("days")) * 86400
                        + int(match.group("hours")) * 3600
                        + int(match.group("minutes")) * 60
                        + int(match.group("seconds"))
                    )

        return 0

    def _coerce_float(self, value: Any) -> float:
        """Best-effort float conversion for OMV responses."""
        if value in (None, ""):
            return 0.0
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip().replace(",", ".")
            match = re.search(r"-?\d+(?:\.\d+)?", normalized)
            if match:
                return float(match.group(0))
        return 0.0

    def _coerce_optional_float(self, value: Any) -> float | None:
        """Convert numeric values while preserving missing values as None."""
        if value in (None, ""):
            return None
        return round(self._coerce_float(value), 1)

    def _coerce_datetime(self, value: Any) -> datetime | None:
        """Best-effort conversion for timestamp-like OMV values."""
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, int | float):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=UTC)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            numeric_match = re.fullmatch(r"-?\d+(?:\.\d+)?", stripped)
            if numeric_match:
                return self._coerce_datetime(float(stripped))
            normalized = stripped.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    def _coerce_bool(self, value: Any) -> bool:
        """Normalize OMV bool-like values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on", "up"}
        return False

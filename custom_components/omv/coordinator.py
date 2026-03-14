"""DataUpdateCoordinator for OpenMediaVault."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .exceptions import OMVApiError, OMVConnectionError
from .omv_api import OMVAPI

_LOGGER = logging.getLogger(__name__)
_HWINFO_REFRESH_MULTIPLIER = 60
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
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.config_entry = config_entry
        self.api = api
        self.omv_version = 0
        self.smart_disabled = smart_disabled
        self._hwinfo: dict[str, Any] = {}
        self._hwinfo_counter = 0
        self._network_counters: dict[str, dict[str, float]] = {}

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all coordinator data from OMV."""
        try:
            self._hwinfo_counter += 1
            if self._hwinfo_counter >= _HWINFO_REFRESH_MULTIPLIER or not self._hwinfo:
                self._hwinfo = await self._async_get_hwinfo()
                self._hwinfo_counter = 0

            data: dict[str, Any] = {"hwinfo": self._hwinfo}

            filesystems = await self._fetch_or_empty(
                "FileSystemMgmt", "enumerateFilesystems"
            )
            services = await self._fetch_or_empty("Services", "getStatus")
            network = await self._fetch_or_empty("Network", "enumerateDevices")
            disks = await self._fetch_or_empty("DiskMgmt", "enumerateDevices")

            data["fs"] = self._normalize_filesystems(filesystems)
            data["service"] = self._normalize_services(services)
            data["network"] = self._normalize_network(network)

            normalized_disks = self._normalize_disks(disks)
            if not self.smart_disabled:
                data["smart"] = await self._async_get_smart(normalized_disks)
            else:
                data["smart"] = []
            data["disk"] = normalized_disks

            data["compose"] = self._normalize_named_collection(
                await self._fetch_optional("compose", "getContainerList")
            )
            data["kvm"] = self._normalize_named_collection(
                await self._fetch_optional("Kvm", "getVmList", {"start": 0, "limit": 999})
            )
            data["zfs"] = self._normalize_named_collection(
                await self._fetch_optional("zfs", "listPools")
            )
            data["raid"] = self._read_mdstat()

            return data
        except OMVConnectionError as err:
            raise UpdateFailed(f"Cannot connect to OMV: {err}") from err
        except OMVApiError as err:
            raise UpdateFailed(f"OMV API error: {err}") from err

    async def _async_get_hwinfo(self) -> dict[str, Any]:
        """Fetch and normalize OMV hardware information."""
        system_info = await self.api.async_call("System", "getInformation")
        hwinfo = self._normalize_hwinfo(system_info if isinstance(system_info, dict) else {})

        cpu_temp = await self._fetch_optional("CpuTemp", "get")
        if isinstance(cpu_temp, dict) and cpu_temp.get("cputemp") is not None:
            hwinfo["cputemp"] = round(self._coerce_float(cpu_temp.get("cputemp")), 1)

        return hwinfo

    async def _async_get_smart(self, disks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch SMART information and merge relevant fields into disks."""
        method = "getListBg" if self.omv_version >= 7 else "getList"
        params = {"start": 0, "limit": 100} if method == "getListBg" else None
        response = await self.api.async_call("Smart", method, params)

        if method == "getListBg" and not self._response_contains_records(response):
            _LOGGER.debug(
                "Smart.getListBg returned %s for OMV %s; falling back to Smart.getList",
                type(response).__name__,
                self.omv_version,
            )
            response = await self.api.async_call("Smart", "getList", params)

        smart_records = self._records_from_response(response)

        by_device: dict[str, dict[str, Any]] = {}
        for record in smart_records:
            devicename = str(record.get("devicename") or "")
            devicefile = str(record.get("devicefile") or record.get("canonicaldevicefile") or "")
            if devicename:
                by_device[devicename] = record
            if devicefile:
                by_device[devicefile] = record

        for disk in disks:
            key = str(disk.get("devicename") or "")
            canonical = str(disk.get("canonicaldevicefile") or "")
            smart_record = by_device.get(key) or by_device.get(canonical)
            if smart_record:
                disk["temperature"] = self._coerce_float(
                    smart_record.get("temperature", disk.get("temperature", 0))
                )
                disk["overallstatus"] = str(
                    smart_record.get("overallstatus", disk.get("overallstatus", "unknown"))
                )

            if key.startswith(("mmcblk", "sr", "bcache")) or not canonical:
                continue

            attributes = await self._fetch_optional(
                "Smart",
                "getAttributes",
                {"devicefile": canonical},
            )
            for attribute in self._records_from_response(attributes):
                attrname = attribute.get("attrname")
                if attrname not in _SMART_ATTRIBUTE_NAMES:
                    continue
                raw_value = attribute.get("rawvalue", "unknown")
                if isinstance(raw_value, str) and " " in raw_value:
                    raw_value = raw_value.split(" ", 1)[0]
                disk[attrname] = raw_value

        return smart_records

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
        except (OMVApiError, OMVConnectionError):
            return []

    def _normalize_hwinfo(self, info: dict[str, Any]) -> dict[str, Any]:
        """Normalize the OMV system information payload."""
        mem_total = int(self._coerce_float(info.get("memTotal")))
        mem_used = int(self._coerce_float(info.get("memUsed")))
        uptime_raw = info.get("uptime", 0)
        uptime_seconds = self._parse_uptime_seconds(uptime_raw)
        available_updates = int(self._coerce_float(info.get("availablePkgUpdates", 0)))
        load_average = info.get("loadAverage") if isinstance(info.get("loadAverage"), dict) else {}

        return {
            "hostname": str(info.get("hostname", "unknown")),
            "version": str(info.get("version", "unknown")),
            "cpuUtilization": round(self._coerce_float(info.get("cpuUtilization")), 1),
            "cputemp": round(self._coerce_float(info.get("cputemp")), 1),
            "memTotal": mem_total,
            "memUsed": mem_used,
            "memUsage": round((mem_used / mem_total) * 100, 1) if mem_total else 0,
            "loadAverage": {
                "1min": self._coerce_float(load_average.get("1min")),
                "5min": self._coerce_float(load_average.get("5min")),
                "15min": self._coerce_float(load_average.get("15min")),
            },
            "uptime": uptime_raw,
            "uptimeEpoch": datetime.now(timezone.utc) - timedelta(seconds=uptime_seconds),
            "configDirty": self._coerce_bool(info.get("configDirty")),
            "rebootRequired": self._coerce_bool(info.get("rebootRequired")),
            "availablePkgUpdates": available_updates,
            "pkgUpdatesAvailable": available_updates > 0,
        }

    def _normalize_filesystems(self, response: Any) -> list[dict[str, Any]]:
        """Normalize filesystem data into entity-friendly records."""
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
            devicename = str(
                record.get("devicename")
                or record.get("devicefile")
                or record.get("parentdevicefile")
                or identifier
            )
            if devicename.startswith("mapper/"):
                devicename = devicename[len("mapper/") :]

            filesystems.append(
                {
                    "uuid": identifier,
                    "label": str(record.get("label") or devicename),
                    "type": filesystem_type or "unknown",
                    "mounted": self._coerce_bool(record.get("mounted")),
                    "devicename": devicename,
                    "parentdevicefile": str(record.get("parentdevicefile", "")),
                    "mountdir": str(record.get("mountdir") or record.get("dir") or ""),
                    "size": round(size_bytes / 1073741824, 1),
                    "available": round(available_bytes / 1073741824, 1),
                    "used": round(used_bytes / 1073741824, 1),
                    "percentage": round(self._coerce_float(record.get("percentage")), 1),
                    "_readonly": self._coerce_bool(record.get("_readonly")),
                    "_used": self._coerce_bool(record.get("_used")),
                    "propreadonly": self._coerce_bool(record.get("propreadonly")),
                }
            )

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
            current_rx = self._coerce_float(
                stats.get("rx_bytes", stats.get("rx_packets", 0))
            )
            current_tx = self._coerce_float(
                stats.get("tx_bytes", stats.get("tx_packets", 0))
            )
            previous = self._network_counters.get(uuid)

            if previous is None:
                rx_rate = 0.0
                tx_rate = 0.0
            else:
                rx_rate = round(max(0.0, current_rx - previous["rx"]) * 8 / interval_seconds, 2)
                tx_rate = round(max(0.0, current_tx - previous["tx"]) * 8 / interval_seconds, 2)

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
            disk = {
                "devicename": devicename,
                "canonicaldevicefile": str(record.get("canonicaldevicefile") or ""),
                "size": record.get("size", "unknown"),
                "vendor": str(record.get("vendor") or "unknown"),
                "model": str(record.get("model") or "unknown"),
                "description": str(record.get("description") or "unknown"),
                "serialnumber": str(record.get("serialnumber") or "unknown"),
                "israid": self._coerce_bool(record.get("israid")),
                "isroot": self._coerce_bool(record.get("isroot")),
                "isreadonly": self._coerce_bool(record.get("isreadonly")),
                "temperature": round(self._coerce_float(record.get("temperature")), 1),
                "overallstatus": str(record.get("overallstatus") or "unknown"),
            }
            for attribute in _SMART_ATTRIBUTE_NAMES:
                disk[attribute] = record.get(attribute, "unknown")
            disks.append(disk)

        return disks

    def _normalize_named_collection(self, response: Any) -> list[dict[str, Any]]:
        """Normalize plugin collections that already contain flat object data."""
        return self._records_from_response(response)

    def _read_mdstat(self) -> list[dict[str, Any]]:
        """Parse RAID status from /proc/mdstat if available."""
        mdstat_path = Path("/proc/mdstat")
        if not mdstat_path.exists():
            return []

        try:
            lines = mdstat_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        raids: list[dict[str, Any]] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line.startswith("md"):
                index += 1
                continue

            parts = line.split()
            device = parts[0]
            state = parts[2] if len(parts) > 2 else "unknown"
            level = parts[3] if len(parts) > 3 else "unknown"

            index += 1
            health_line = lines[index].strip() if index < len(lines) else ""
            match = re.search(r"\[([^\]]+)\]", health_line)
            health_indicator = match.group(1) if match else ""
            action = None
            action_percent = None
            status = "clean"

            if index + 1 < len(lines) and re.search(r"(resync|check|recover)", lines[index + 1]):
                index += 1
                action_line = lines[index].strip()
                action_match = re.search(r"(resync|check|recover)\s*=\s*([\d.]+)%", action_line)
                if action_match:
                    action = action_match.group(1)
                    action_percent = float(action_match.group(2))

            if action:
                status = action
            elif "_" in health_indicator:
                status = "degraded"

            raids.append(
                {
                    "device": device,
                    "state": state,
                    "level": level,
                    "health": status,
                    "health_indicator": health_indicator,
                    "action_percent": action_percent,
                }
            )
            index += 1

        return raids

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
        if isinstance(value, (int, float)):
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
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip().replace(",", ".")
            match = re.search(r"-?\d+(?:\.\d+)?", normalized)
            if match:
                return float(match.group(0))
        return 0.0

    def _coerce_bool(self, value: Any) -> bool:
        """Normalize OMV bool-like values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on", "up"}
        return False
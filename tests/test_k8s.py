"""Tests for the Kubernetes (openmediavault-k8s) workload feature."""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.omv import _async_cleanup_stale_registry_entries
from custom_components.omv.button import (
    OMVK8sRecreatePodsButton,
    get_expected_button_unique_ids,
)
from custom_components.omv.button import async_setup_entry as async_setup_buttons
from custom_components.omv.const import CONF_SELECTED_K8S_WORKLOADS, DOMAIN
from custom_components.omv.coordinator import (
    _K8S_NODES_REFRESH_MULTIPLIER,
    K8S_WORKLOAD_STATES,
    OMVDataUpdateCoordinator,
)
from custom_components.omv.diagnostics import async_get_config_entry_diagnostics
from custom_components.omv.entity import get_k8s_workload_device_info
from custom_components.omv.exceptions import OMVApiError, OMVConnectionError
from custom_components.omv.sensor import (
    OMVSensor,
    get_expected_sensor_registry_state,
)
from custom_components.omv.sensor import async_setup_entry as async_setup_sensors
from custom_components.omv.sensor_types import K8S_SENSORS, K8S_WORKLOAD_SENSORS

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "k8s_kubectl.json").read_text(encoding="utf-8"))
_K3S_SERVICE = {"name": "k3s", "title": "Kubernetes", "enabled": True, "running": True}
_ALL_KEYS = [
    "actual-app/deployment/actual",
    "broken-app/deployment/broken",
    "db-app/statefulset/db",
    "intel-gpu-plugin-app/daemonset/intel-gpu-plugin",
    "n8n-app/deployment/n8n",
    "omv-ha-probe/deployment/probe-slow",
    "paused-app/deployment/paused",
    "web-app/deployment/web",
]


def _payload(key: str) -> Any:
    """Return a deep copy of one fixture payload (workloads_and_pods/pod_metrics/nodes)."""
    return copy.deepcopy(_FIXTURE[key])


def _new_coordinator(hass, config_entry) -> OMVDataUpdateCoordinator:
    """Return a bare coordinator with a mocked API (no data loaded)."""
    config_entry.add_to_hass(hass)
    api = Mock()
    api.base_url = "http://192.0.2.10:80"
    api.async_call = AsyncMock()
    return OMVDataUpdateCoordinator(hass, config_entry, api, scan_interval=60)


def _normalized(coordinator: OMVDataUpdateCoordinator) -> list[dict[str, Any]]:
    """Normalize the full fixture."""
    return coordinator._normalize_k8s_workloads(
        _payload("workloads_and_pods"),
        _payload("pod_metrics"),
        _payload("nodes")["data"],
    )


def _by_key(workloads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index workloads by workload_key."""
    return {workload["workload_key"]: workload for workload in workloads}


def _make_async_call(
    services: list[dict[str, Any]], kubectl: dict[str, Any]
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build an OMV RPC fake; ``kubectl`` maps Kubectl.getList types to a payload or exception."""

    async def async_call(service: str, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Return the canned response for one RPC (or raise the canned error)."""
        params = params or {}
        if service == "Kubectl":
            result = kubectl[params["type"]] if method == "getList" else None
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)
        responses = {
            ("System", "getInformation"): {"hostname": "nas", "version": "8.1.2-1"},
            ("CpuTemp", "get"): {"cputemp": 55.0},
            ("FileSystemMgmt", "enumerateFilesystems"): [],
            ("Services", "getStatus"): services,
            ("Network", "enumerateDevices"): [],
            ("DiskMgmt", "enumerateDevices"): [],
            ("Smart", "getListBg"): [],
            ("Smart", "getList"): {"data": [], "total": 0},
            ("compose", "getContainerList"): {"data": []},
            ("compose", "getFileList"): {"data": []},
            ("Compose", "getVolumesBg"): {"data": []},
            ("Kvm", "getVmList"): {"data": [], "total": 0},
            ("TempMon", "getSensorsList"): {"data": [], "total": 0},
            ("zfs", "listPools"): [],
            ("Nut", "getStats"): "Service disabled",
            ("Rsync", "getList"): [],
            ("Cron", "getList"): [],
            ("MdMgmt", "enumerateDevices"): [],
        }
        return responses[(service, method)]

    return async_call


def _kubectl_calls(api: Mock) -> list[str]:
    """Return the Kubectl.getList ``type`` params in call order."""
    return [call.args[2]["type"] for call in api.async_call.await_args_list if call.args[0] == "Kubectl"]


def _full_kubectl() -> dict[str, Any]:
    """Return Kubectl.getList payloads for all three types from the fixture."""
    return {
        "deployments,statefulsets,daemonsets,pods": _payload("workloads_and_pods"),
        "pods.metrics.k8s.io": _payload("pod_metrics"),
        "nodes": _payload("nodes"),
    }


def _attach_k8s(coordinator: OMVDataUpdateCoordinator) -> list[dict[str, Any]]:
    """Load the normalized fixture into the conftest coordinator's data."""
    workloads = _normalized(coordinator)
    services = [*coordinator.data.get("service", []), _K3S_SERVICE]
    coordinator.data = {
        **coordinator.data,
        "service": services,
        "k8s_workloads": workloads,
        "k8s_summary": coordinator._summarize_k8s(workloads, services),
    }
    return workloads


# --- quantity parser --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", 2.0),
        ("0.5", 0.5),
        ("250m", 0.25),
        ("34111n", 0.000034111),
        ("12u", 0.000012),
        ("3500m", 3.5),
        ("54540Ki", 54540 * 1024.0),
        ("512Mi", 512 * 1024.0**2),
        ("1Gi", 1024.0**3),
        ("1Ti", 1024.0**4),
        ("1000k", 1_000_000.0),
        ("1M", 1_000_000.0),
        ("1G", 1_000_000_000.0),
        ("123456", 123456.0),
        ("1e3", 1000.0),
        ("128974848e0", 128974848.0),
        (" 4 ", 4.0),
        (4, 4.0),
        (0.25, 0.25),
        ("0", 0.0),
    ],
)
def test_parse_k8s_quantity_valid(value: Any, expected: float) -> None:
    """Kubernetes CPU and memory quantities are converted to cores/bytes."""
    assert OMVDataUpdateCoordinator._parse_k8s_quantity(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [None, "", "abc", "12x", "1KiB", "Ki", "m", "1.2.3", "-1", "-250m", "1e999", True, [], {}, "nan", "inf"],
)
def test_parse_k8s_quantity_rejects_garbage(value: Any) -> None:
    """Anything that is not a finite, non-negative quantity yields None."""
    assert OMVDataUpdateCoordinator._parse_k8s_quantity(value) is None


# --- normalization ----------------------------------------------------------


async def test_normalize_maps_pods_and_derives_every_state(hass, config_entry) -> None:
    """Pods map to their controllers; replica counters drive every enum value."""
    coordinator = _new_coordinator(hass, config_entry)
    workloads = _normalized(coordinator)
    by_key = _by_key(workloads)

    # Sorted, one record per Deployment/StatefulSet/DaemonSet; the Job pod
    # and the bare pod never produce or attach to a workload.
    assert [workload["workload_key"] for workload in workloads] == _ALL_KEYS
    assert not any("janitor" in pod["name"] for w in workloads for pod in w["pods"])
    assert not any(pod["name"] == "debug-shell" for w in workloads for pod in w["pods"])

    assert {key: workload["state"] for key, workload in by_key.items()} == {
        "actual-app/deployment/actual": "running",
        "broken-app/deployment/broken": "unavailable",
        "db-app/statefulset/db": "progressing",
        "intel-gpu-plugin-app/daemonset/intel-gpu-plugin": "running",
        "n8n-app/deployment/n8n": "running",
        "omv-ha-probe/deployment/probe-slow": "running",
        "paused-app/deployment/paused": "scaled_down",
        "web-app/deployment/web": "degraded",
    }
    assert {workload["state"] for workload in workloads} == set(K8S_WORKLOAD_STATES)

    actual = by_key["actual-app/deployment/actual"]
    assert actual["namespace"] == "actual-app"
    assert actual["name"] == "actual"
    assert actual["kind"] == "Deployment"
    assert actual["images"] == ["actualbudget/actual-server:latest"]
    assert [pod["name"] for pod in actual["pods"]] == ["actual-65fd9dc779-b8r2v"]
    assert actual["pods"][0] == {
        "name": "actual-65fd9dc779-b8r2v",
        "phase": "Running",
        "ready": True,
        "restarts": 72,
        "terminating": False,
        "grace_period": 30,
    }
    assert (actual["desired"], actual["ready"], actual["available"], actual["updated"]) == (1, 1, 1, 1)

    daemonset = by_key["intel-gpu-plugin-app/daemonset/intel-gpu-plugin"]
    assert daemonset["kind"] == "DaemonSet"
    assert (daemonset["desired"], daemonset["ready"], daemonset["available"], daemonset["updated"]) == (1, 1, 1, 1)
    assert [pod["name"] for pod in daemonset["pods"]] == ["intel-gpu-plugin-5cqx2"]

    statefulset = by_key["db-app/statefulset/db"]
    assert [pod["name"] for pod in statefulset["pods"]] == ["db-0", "db-1"]
    assert statefulset["images"] == [
        "docker.io/library/postgres:17",
        "quay.io/prometheuscommunity/postgres-exporter:v0.16.0",
    ]
    assert statefulset["pods"][0]["grace_period"] == 60

    web = by_key["web-app/deployment/web"]
    assert (web["desired"], web["ready"], web["updated"]) == (2, 1, 2)
    assert web["restarts"] == 4
    assert [pod["ready"] for pod in web["pods"]] == [True, False]

    # ReplicaSet owner without pod-template-hash label: rsplit fallback.
    broken = by_key["broken-app/deployment/broken"]
    assert [pod["name"] for pod in broken["pods"]] == ["broken-6b9f4d7c8f-qwert"]
    assert broken["ready"] == 0
    assert broken["restarts"] == 12

    paused = by_key["paused-app/deployment/paused"]
    assert paused["desired"] == 0
    assert paused["pods"] == []
    assert paused["restarts"] == 0


async def test_normalize_excludes_terminating_pod_from_restarts(hass, config_entry) -> None:
    """A terminating pod is listed but neither counted in restarts nor live."""
    coordinator = _new_coordinator(hass, config_entry)
    probe = _by_key(_normalized(coordinator))["omv-ha-probe/deployment/probe-slow"]

    assert [(pod["name"], pod["terminating"]) for pod in probe["pods"]] == [
        ("probe-slow-7c7568dffd-d782n", True),
        ("probe-slow-7c7568dffd-x9k2p", False),
    ]
    assert probe["pods"][0]["restarts"] == 5
    assert probe["pods"][0]["grace_period"] == 45
    assert probe["restarts"] == 0


async def test_normalize_sums_metrics_and_cpu_percent(hass, config_entry) -> None:
    """Multi-container pods are summed; CPU percent is relative to node allocatable."""
    coordinator = _new_coordinator(hass, config_entry)
    by_key = _by_key(_normalized(coordinator))

    n8n = by_key["n8n-app/deployment/n8n"]
    # 236910n + 5782605n = 6.019515 millicores; 8612Ki + 519000Ki = 515.25 MiB
    assert n8n["cpu_millicores"] == 6.0
    assert n8n["memory_mib"] == 515.2
    assert n8n["cpu_percent"] == 0.15  # 6.019515 / 4000 * 100
    assert n8n["restarts"] == 82
    assert n8n["images"] == ["docker.n8n.io/n8nio/n8n:2.42.7", "n8nio/runners:2.42.7"]

    actual = by_key["actual-app/deployment/actual"]
    assert actual["cpu_millicores"] == 0.0  # 36353n = 0.036 millicores
    assert actual["memory_mib"] == 53.3
    assert actual["cpu_percent"] == 0.0

    assert by_key["intel-gpu-plugin-app/daemonset/intel-gpu-plugin"]["cpu_millicores"] == 0.2

    web = by_key["web-app/deployment/web"]
    assert web["cpu_millicores"] == 250.0
    assert web["memory_mib"] == 512.0
    assert web["cpu_percent"] == 6.25

    broken = by_key["broken-app/deployment/broken"]
    assert broken["cpu_millicores"] == 0.0
    assert broken["memory_mib"] == 1.0  # 1000k bytes

    # Terminating pod still consumes resources and keeps its metrics.
    assert by_key["omv-ha-probe/deployment/probe-slow"]["memory_mib"] == 0.3

    # No mapped pod has a metrics record (too new / no pods at all) -> None.
    for key in ("db-app/statefulset/db", "paused-app/deployment/paused"):
        assert by_key[key]["cpu_millicores"] is None
        assert by_key[key]["memory_mib"] is None
        assert by_key[key]["cpu_percent"] is None


async def test_normalize_cpu_percent_with_millicore_allocatable(hass, config_entry) -> None:
    """Allocatable CPU given in millicores ("3500m") is honoured."""
    coordinator = _new_coordinator(hass, config_entry)
    nodes = _payload("nodes")
    nodes["data"][0]["status"]["allocatable"]["cpu"] = "3500m"

    workloads = coordinator._normalize_k8s_workloads(_payload("workloads_and_pods"), _payload("pod_metrics"), nodes)
    web = _by_key(workloads)["web-app/deployment/web"]

    assert web["cpu_percent"] == 7.14  # 250 / 3500 * 100


async def test_normalize_without_metrics_or_nodes(hass, config_entry) -> None:
    """Missing metrics blank CPU/memory; missing nodes blank only the percentage."""
    coordinator = _new_coordinator(hass, config_entry)

    without_metrics = coordinator._normalize_k8s_workloads(_payload("workloads_and_pods"), [], _payload("nodes"))
    assert [w["workload_key"] for w in without_metrics] == _ALL_KEYS
    assert all(w["cpu_millicores"] is None and w["memory_mib"] is None for w in without_metrics)
    assert all(w["cpu_percent"] is None for w in without_metrics)

    without_nodes = _by_key(
        coordinator._normalize_k8s_workloads(_payload("workloads_and_pods"), _payload("pod_metrics"), [])
    )
    assert without_nodes["web-app/deployment/web"]["cpu_millicores"] == 250.0
    assert without_nodes["web-app/deployment/web"]["cpu_percent"] is None


async def test_normalize_is_defensive_against_malformed_payloads(hass, config_entry) -> None:
    """Garbage records and missing fields never raise."""
    coordinator = _new_coordinator(hass, config_entry)
    raw = {
        "total": 5,
        "data": [
            "not-a-dict",
            {"kind": "Deployment", "metadata": None, "spec": [], "status": "x"},
            {"kind": "Deployment", "metadata": {"name": "lonely", "namespace": "ns"}},
            {"kind": "Pod", "metadata": {"name": "p", "namespace": "ns", "ownerReferences": "bogus"}},
            {"kind": "Pod", "metadata": {"name": "q", "namespace": "ns", "ownerReferences": [{"kind": "ReplicaSet"}]}},
        ],
    }

    workloads = coordinator._normalize_k8s_workloads(raw, {"data": [{"metadata": {}}, 3]}, None)

    assert len(workloads) == 1
    lonely = workloads[0]
    assert lonely["workload_key"] == "ns/deployment/lonely"
    assert lonely["desired"] == 1  # spec.replicas absent -> default 1
    assert lonely["state"] == "unavailable"
    assert lonely["images"] == []
    assert lonely["pods"] == []


# --- coordinator fetch / gating --------------------------------------------


@pytest.mark.parametrize(
    "services",
    [
        [],
        [{"name": "compose", "title": "Docker Compose", "enabled": True, "running": True}],
        [{**_K3S_SERVICE, "enabled": False, "running": False}],
    ],
)
async def test_no_kubectl_call_without_enabled_k3s_service(hass, config_entry, services) -> None:
    """Without an enabled k3s service no Kubectl RPC is issued and both keys are empty."""
    coordinator = _new_coordinator(hass, config_entry)
    coordinator.api.async_call = AsyncMock(side_effect=_make_async_call(services, _full_kubectl()))
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert _kubectl_calls(coordinator.api) == []
    assert data["k8s_workloads"] == []
    assert data["k8s_summary"] == {}


async def test_update_fetches_workloads_metrics_and_nodes(hass, config_entry) -> None:
    """With k3s enabled the three Kubectl.getList calls are issued with their params."""
    coordinator = _new_coordinator(hass, config_entry)
    coordinator.api.async_call = AsyncMock(side_effect=_make_async_call([_K3S_SERVICE], _full_kubectl()))
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    kubectl_params = [call.args[2] for call in coordinator.api.async_call.await_args_list if call.args[0] == "Kubectl"]
    assert kubectl_params == [
        {"type": "deployments,statefulsets,daemonsets,pods", "start": 0, "limit": -1},
        {"type": "pods.metrics.k8s.io", "start": 0, "limit": -1},
        {"type": "nodes", "start": 0, "limit": -1},
    ]
    assert [w["workload_key"] for w in data["k8s_workloads"]] == _ALL_KEYS
    assert data["k8s_summary"] == {
        "workload_total": 8,
        "workload_running": 4,
        "workload_not_running": 4,
        "pod_total": 9,
        "pod_running": 8,
    }
    assert "k8s_workloads" not in coordinator.degraded_collections


async def test_metrics_failure_keeps_workloads(hass, config_entry) -> None:
    """A missing metrics-server only blanks CPU/memory; workloads stay."""
    kubectl = _full_kubectl()
    kubectl["pods.metrics.k8s.io"] = OMVApiError("RPC Kubectl.getList failed: the server doesn't have a resource type")
    coordinator = _new_coordinator(hass, config_entry)
    coordinator.api.async_call = AsyncMock(side_effect=_make_async_call([_K3S_SERVICE], kubectl))
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert [w["workload_key"] for w in data["k8s_workloads"]] == _ALL_KEYS
    assert all(w["cpu_percent"] is None and w["memory_mib"] is None for w in data["k8s_workloads"])
    assert not coordinator.degraded_collections


async def test_workload_fetch_failure_marks_collection_degraded(hass, config_entry) -> None:
    """A failed workload fetch is flagged (Issue #118) and skips the follow-up calls."""
    kubectl = _full_kubectl()
    kubectl["deployments,statefulsets,daemonsets,pods"] = OMVConnectionError("timeout")
    coordinator = _new_coordinator(hass, config_entry)
    coordinator.api.async_call = AsyncMock(side_effect=_make_async_call([_K3S_SERVICE], kubectl))
    await coordinator.async_init({"hostname": "nas", "version": "8.1.2-1"})

    data = await coordinator._async_update_data()

    assert data["k8s_workloads"] == []
    assert "k8s_workloads" in coordinator.degraded_collections
    assert _kubectl_calls(coordinator.api) == ["deployments,statefulsets,daemonsets,pods"]


async def test_node_cache_refresh_cadence(hass, config_entry) -> None:
    """Nodes are fetched on the first cycle, then once per refresh multiplier."""
    coordinator = _new_coordinator(hass, config_entry)
    coordinator.api.async_call = AsyncMock(return_value=_payload("nodes"))

    for _ in range(_K8S_NODES_REFRESH_MULTIPLIER):
        nodes = await coordinator._async_fetch_k8s_nodes()
    assert coordinator.api.async_call.await_count == 1
    assert nodes[0]["metadata"]["name"] == "omv-node"

    await coordinator._async_fetch_k8s_nodes()
    assert coordinator.api.async_call.await_count == 2


async def test_node_cache_retries_while_empty_and_keeps_old_on_failure(hass, config_entry) -> None:
    """An empty cache is refetched every cycle; a failed refresh keeps the cache."""
    coordinator = _new_coordinator(hass, config_entry)
    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("boom"))

    assert await coordinator._async_fetch_k8s_nodes() == []
    assert await coordinator._async_fetch_k8s_nodes() == []
    assert coordinator.api.async_call.await_count == 2

    coordinator.api.async_call = AsyncMock(return_value=_payload("nodes"))
    cached = await coordinator._async_fetch_k8s_nodes()
    assert len(cached) == 1

    coordinator.api.async_call = AsyncMock(side_effect=OMVApiError("boom"))
    coordinator._k8s_nodes_counter = _K8S_NODES_REFRESH_MULTIPLIER
    assert await coordinator._async_fetch_k8s_nodes() == cached


# --- selection / inventory --------------------------------------------------


async def test_filter_data_by_selection_k8s_workloads(hass, config_entry) -> None:
    """No selection passes everything; a selection filters and re-summarizes."""
    coordinator = _new_coordinator(hass, config_entry)
    workloads = _normalized(coordinator)
    data = {"service": [_K3S_SERVICE], "k8s_workloads": workloads, "k8s_summary": {}}

    unfiltered = coordinator.filter_data_by_selection(data, {})
    assert [w["workload_key"] for w in unfiltered["k8s_workloads"]] == _ALL_KEYS
    assert unfiltered["k8s_summary"]["workload_total"] == 8

    selected = coordinator.filter_data_by_selection(
        data,
        {CONF_SELECTED_K8S_WORKLOADS: ["n8n-app/deployment/n8n", "web-app/deployment/web"]},
    )
    assert [w["workload_key"] for w in selected["k8s_workloads"]] == [
        "n8n-app/deployment/n8n",
        "web-app/deployment/web",
    ]
    assert selected["k8s_summary"] == {
        "workload_total": 2,
        "workload_running": 1,
        "workload_not_running": 1,
        "pod_total": 3,
        "pod_running": 2,
    }
    # Pure: the input is untouched and every key is passed through.
    assert len(data["k8s_workloads"]) == 8
    assert coordinator.filter_data_by_selection({}, {})["k8s_workloads"] == []
    assert coordinator.filter_data_by_selection({}, {})["k8s_summary"] == {}


async def test_build_inventory_lists_k8s_workloads(hass, config_entry) -> None:
    """The options flow offers every workload with a namespace / name (kind) label."""
    coordinator = _new_coordinator(hass, config_entry)
    inventory = OMVDataUpdateCoordinator.build_inventory({"k8s_workloads": _normalized(coordinator)})

    options = inventory[CONF_SELECTED_K8S_WORKLOADS]
    assert [option["value"] for option in options] == _ALL_KEYS
    assert {"value": "n8n-app/deployment/n8n", "label": "n8n-app / n8n (Deployment)"} in options
    assert {
        "value": "intel-gpu-plugin-app/daemonset/intel-gpu-plugin",
        "label": "intel-gpu-plugin-app / intel-gpu-plugin (DaemonSet)",
    } in options


# --- entities ---------------------------------------------------------------


async def test_sensor_setup_creates_workload_and_summary_sensors(coordinator, config_entry) -> None:
    """Every workload gets all four sensors (even without metrics), plus hub summaries."""
    _attach_k8s(coordinator)
    added: list[OMVSensor] = []

    await async_setup_sensors(coordinator.hass, config_entry, added.extend)

    entry_id = config_entry.entry_id
    unique_ids = {entity.unique_id for entity in added}
    for key in _ALL_KEYS:
        for description in K8S_WORKLOAD_SENSORS:
            assert f"{entry_id}-{description.key}-{key}" in unique_ids
    for description in K8S_SENSORS:
        assert f"{entry_id}-{description.key}" in unique_ids

    db_cpu = next(e for e in added if e.unique_id == f"{entry_id}-k8s_workload_cpu-db-app/statefulset/db")
    assert db_cpu.native_value is None

    expected_ids, expected_devices = get_expected_sensor_registry_state(coordinator)
    assert {entity.unique_id for entity in added if "k8s" in entity.unique_id} <= expected_ids
    assert (DOMAIN, f"{entry_id}:k8s_workload:db-app/statefulset/db") in expected_devices


async def test_no_k8s_entities_without_k3s(coordinator, config_entry) -> None:
    """Existing installs without the plugin get no Kubernetes entities."""
    added: list[Any] = []
    await async_setup_sensors(coordinator.hass, config_entry, added.extend)
    await async_setup_buttons(coordinator.hass, config_entry, added.extend)

    assert not any("k8s" in str(entity.unique_id) for entity in added)


async def test_workload_sensors_states_attributes_and_device(coordinator) -> None:
    """State/restarts/CPU/memory sensors read the record and share one workload device."""
    _attach_k8s(coordinator)
    key = "n8n-app/deployment/n8n"
    state, restarts, cpu, memory = (OMVSensor(coordinator, d, item_key=key) for d in K8S_WORKLOAD_SENSORS)

    assert state.native_value == "running"
    assert state.device_class == SensorDeviceClass.ENUM
    assert state.options == list(K8S_WORKLOAD_STATES)
    assert state.icon == "mdi:kubernetes"
    assert state.extra_state_attributes == {
        "namespace": "n8n-app",
        "kind": "Deployment",
        "name": "n8n",
        "desired_replicas": 1,
        "ready_replicas": 1,
        "available_replicas": 1,
        "updated_replicas": 1,
        "pod_count": 1,
        "pods": ["n8n-6d5786467f-6kwgb"],
        "images": ["docker.n8n.io/n8nio/n8n:2.42.7", "n8nio/runners:2.42.7"],
    }
    assert state._attr_translation_placeholders == {"resource": "n8n"}

    assert restarts.native_value == 82
    assert restarts.state_class == SensorStateClass.TOTAL_INCREASING
    assert cpu.native_value == 0.15
    assert cpu.native_unit_of_measurement == "%"
    assert cpu.extra_state_attributes == {"cpu_millicores": 6.0}
    assert memory.native_value == 515.2
    assert memory.device_class == SensorDeviceClass.DATA_SIZE
    assert memory.native_unit_of_measurement == "MiB"

    entry_id = coordinator.config_entry.entry_id
    for sensor in (state, restarts, cpu, memory):
        assert sensor.device_info["identifiers"] == {(DOMAIN, f"{entry_id}:k8s_workload:{key}")}
    device = state.device_info
    assert device["name"] == "Kubernetes n8n-app/n8n"
    assert device["manufacturer"] == "Kubernetes"
    assert device["model"] == "Deployment"
    assert device["sw_version"] == "2.42.7"
    assert device["via_device_id"] == coordinator.hub_device_id
    assert state._attr_suggested_object_id == "nas_k8s_n8n_app_n8n_state"


@pytest.mark.parametrize(
    ("images", "expected"),
    [
        (["docker.io/library/nginx:1.27"], "1.27"),
        (["registry.local:5000/app"], None),
        (["registry.local:5000/app:2.0@sha256:abc"], "2.0"),
        (["nginx@sha256:abc"], None),
        ([], None),
    ],
)
async def test_workload_device_sw_version_from_image_tag(coordinator, images, expected) -> None:
    """sw_version is the first image's tag; registry ports and digests are not tags."""
    device = get_k8s_workload_device_info(
        coordinator,
        {"workload_key": "ns/deployment/x", "namespace": "ns", "name": "x", "kind": "Deployment", "images": images},
    )
    assert device.get("sw_version") == expected


async def test_k8s_summary_sensors_expose_counts(coordinator) -> None:
    """Hub-level summary sensors read k8s_summary."""
    _attach_k8s(coordinator)
    total, running, not_running = (OMVSensor(coordinator, d) for d in K8S_SENSORS)

    assert (total.native_value, running.native_value, not_running.native_value) == (8, 4, 4)
    assert total.extra_state_attributes["pod_total"] == 9
    assert total._attr_suggested_object_id == "nas_k8s_workload_total"


# --- recreate-pods button ---------------------------------------------------


async def test_button_setup_and_expected_unique_ids(coordinator, config_entry) -> None:
    """One recreate-pods button per workload, included in the cleanup unique-id set."""
    _attach_k8s(coordinator)
    added: list[Any] = []
    await async_setup_buttons(coordinator.hass, config_entry, added.extend)

    buttons = [entity for entity in added if isinstance(entity, OMVK8sRecreatePodsButton)]
    assert len(buttons) == len(_ALL_KEYS)
    expected = get_expected_button_unique_ids(config_entry, coordinator)
    for key in _ALL_KEYS:
        assert f"{config_entry.entry_id}-k8s_recreate_pods-{key}" in expected
    button = next(b for b in buttons if b.unique_id.endswith("-n8n-app/deployment/n8n"))
    assert button.icon == "mdi:restart-alert"
    assert button.translation_key == "k8s_workload_recreate_pods"
    assert button.device_info["identifiers"] == {
        (DOMAIN, f"{config_entry.entry_id}:k8s_workload:n8n-app/deployment/n8n")
    }


def _button(coordinator: OMVDataUpdateCoordinator, key: str) -> OMVK8sRecreatePodsButton:
    """Build the recreate-pods button for one workload of the coordinator data."""
    workload = next(w for w in coordinator.data["k8s_workloads"] if w["workload_key"] == key)
    return OMVK8sRecreatePodsButton(coordinator, workload)


async def test_button_deletes_each_live_pod_with_grace_based_timeout(coordinator) -> None:
    """One Kubectl.delete per non-terminating pod, sequentially, with clamped timeouts."""
    _attach_k8s(coordinator)
    coordinator.api.async_call = AsyncMock(return_value=None)
    coordinator.async_request_refresh = AsyncMock()

    await _button(coordinator, "omv-ha-probe/deployment/probe-slow").async_press()
    await _button(coordinator, "db-app/statefulset/db").async_press()

    calls = [(call.args, call.kwargs) for call in coordinator.api.async_call.await_args_list]
    assert calls == [
        (
            ("Kubectl", "delete", {"type": "pod", "name": "probe-slow-7c7568dffd-x9k2p", "namespace": "omv-ha-probe"}),
            {"max_retries": 0, "timeout": 60.0},
        ),
        (
            ("Kubectl", "delete", {"type": "pod", "name": "db-0", "namespace": "db-app"}),
            {"max_retries": 0, "timeout": 75.0},
        ),
        (
            ("Kubectl", "delete", {"type": "pod", "name": "db-1", "namespace": "db-app"}),
            {"max_retries": 0, "timeout": 75.0},
        ),
    ]
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.parametrize(("grace", "expected"), [(0, 30.0), (30, 45.0), (45, 60.0), (70, 85.0), (600, 85.0)])
async def test_recreate_pods_timeout_is_clamped(coordinator, grace, expected) -> None:
    """Per-call timeout = clamp(grace_period + 15, 30, 85)."""
    coordinator.api.async_call = AsyncMock(return_value=None)
    workload = {
        "namespace": "ns",
        "pods": [{"name": "p-1", "terminating": False, "grace_period": grace}],
    }

    assert await coordinator.async_k8s_recreate_pods(workload) == 1
    assert coordinator.api.async_call.await_args.kwargs["timeout"] == expected


def _client_timeout_error() -> OMVConnectionError:
    """Build the exception chain OMVAPI.async_call raises for a client timeout."""
    raw = OMVConnectionError("Connection to 192.0.2.10 failed: ")
    raw.__cause__ = TimeoutError()
    outer = OMVConnectionError("Failed to reach OMV after 0 retries: ")
    outer.__cause__ = raw
    return outer


async def test_button_timeout_logs_warning_and_continues(coordinator, caplog) -> None:
    """A client timeout means 'still terminating': warn, do not raise, go on."""
    _attach_k8s(coordinator)
    coordinator.api.async_call = AsyncMock(side_effect=[_client_timeout_error(), None])
    coordinator.async_request_refresh = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="custom_components.omv.coordinator"):
        await _button(coordinator, "db-app/statefulset/db").async_press()

    assert coordinator.api.async_call.await_count == 2
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "db-app/db-0" in warnings[0].getMessage()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [OMVApiError("RPC Kubectl.delete failed: NotFound"), OMVConnectionError("Connection refused")],
)
async def test_button_other_errors_raise_translated_error(coordinator, error) -> None:
    """Non-timeout failures stop the command and surface k8s_command_failed."""
    _attach_k8s(coordinator)
    coordinator.api.async_call = AsyncMock(side_effect=error)
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError) as exc_info:
        await _button(coordinator, "db-app/statefulset/db").async_press()

    assert exc_info.value.translation_key == "k8s_command_failed"
    assert exc_info.value.translation_placeholders == {"resource": "db-app/db", "command": "recreate pods"}
    assert coordinator.api.async_call.await_count == 1
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(
    ("namespace", "pod_name"),
    [
        ("Bad_NS", "ok-pod"),
        ("ok-ns", "pod; rm -rf /"),
        ("ok-ns", "-leading-dash"),
        ("ok-ns", "x" * 254),
        ("", "ok-pod"),
        ("ok-ns", None),
    ],
)
async def test_recreate_pods_rejects_invalid_names(coordinator, namespace, pod_name) -> None:
    """Names that are not DNS-1123 are rejected before any RPC is issued."""
    coordinator.api.async_call = AsyncMock()
    workload = {
        "namespace": namespace,
        "pods": [{"name": "valid-first", "terminating": False}, {"name": pod_name, "terminating": False}],
    }

    with pytest.raises(ValueError):
        await coordinator.async_k8s_recreate_pods(workload)
    coordinator.api.async_call.assert_not_awaited()


async def test_button_invalid_name_raises_translated_error(coordinator) -> None:
    """The button maps a rejected name to the translated HomeAssistantError."""
    _attach_k8s(coordinator)
    coordinator.data["k8s_workloads"][0]["pods"][0]["name"] = "Invalid Name"
    coordinator.api.async_call = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError) as exc_info:
        await _button(coordinator, coordinator.data["k8s_workloads"][0]["workload_key"]).async_press()

    assert exc_info.value.translation_key == "k8s_command_failed"
    coordinator.api.async_call.assert_not_awaited()


async def test_button_uses_current_pods_and_fails_for_vanished_workload(coordinator) -> None:
    """Pod names are read at press time; a vanished workload raises."""
    _attach_k8s(coordinator)
    button = _button(coordinator, "actual-app/deployment/actual")
    coordinator.api.async_call = AsyncMock(return_value=None)
    coordinator.async_request_refresh = AsyncMock()

    workload = next(w for w in coordinator.data["k8s_workloads"] if w["workload_key"] == "actual-app/deployment/actual")
    workload["pods"] = [{**workload["pods"][0], "name": "actual-65fd9dc779-newpd"}]
    await button.async_press()
    assert coordinator.api.async_call.await_args.args[2]["name"] == "actual-65fd9dc779-newpd"

    coordinator.data["k8s_workloads"] = []
    with pytest.raises(HomeAssistantError):
        await button.async_press()


# --- diagnostics / registry cleanup ----------------------------------------


async def test_diagnostics_include_k8s_workloads(hass, coordinator, config_entry) -> None:
    """Diagnostics carry the k8s collections (names/images/counts only)."""
    _attach_k8s(coordinator)

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    workloads = result["data"]["k8s_workloads"]
    assert [w["workload_key"] for w in workloads] == _ALL_KEYS
    assert result["data"]["k8s_summary"]["workload_total"] == 8
    assert "uid" not in json.dumps(workloads)


def _register_stale_workload(hass, config_entry) -> tuple[str, str]:
    """Register a device + entity for a workload that no longer exists."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    key = "old-app/deployment/gone"
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{config_entry.entry_id}:k8s_workload:{key}")},
        name="Kubernetes old-app/gone",
    )
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{config_entry.entry_id}-k8s_workload_state-{key}",
        config_entry=config_entry,
        device_id=device.id,
    )
    return key, device.id


async def test_cleanup_prunes_removed_workload(hass, coordinator, config_entry) -> None:
    """A deleted/de-selected workload loses its entities and device; live ones stay."""
    _attach_k8s(coordinator)
    key, device_id = _register_stale_workload(hass, config_entry)
    live_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        **get_k8s_workload_device_info(coordinator, coordinator.data["k8s_workloads"][0]),
    )

    await _async_cleanup_stale_registry_entries(hass, config_entry, coordinator)

    assert dr.async_get(hass).async_get(device_id) is None
    assert (
        er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{config_entry.entry_id}-k8s_workload_state-{key}")
        is None
    )
    assert dr.async_get(hass).async_get(live_device.id) is not None


async def test_cleanup_skipped_when_kubectl_fetch_failed(hass, coordinator, config_entry) -> None:
    """Issue #118 guard: a failed Kubectl fetch must not prune workload entries."""
    _attach_k8s(coordinator)
    coordinator.data["k8s_workloads"] = []
    key, device_id = _register_stale_workload(hass, config_entry)
    coordinator._degraded_collections = {"k8s_workloads"}

    await _async_cleanup_stale_registry_entries(hass, config_entry, coordinator)

    assert dr.async_get(hass).async_get(device_id) is not None
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{config_entry.entry_id}-k8s_workload_state-{key}")


async def test_options_flow_offers_k8s_workload_selection(hass, config_entry) -> None:
    """The options flow lists live workloads and pre-selects all of them on first use."""
    inventory = OMVDataUpdateCoordinator.build_inventory(
        {"k8s_workloads": _normalized(_new_coordinator(hass, config_entry))}
    )
    config_entry.runtime_data = type("RuntimeCoordinator", (), {"get_live_inventory": lambda self=None: inventory})()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] == "form"
    marker = next(m for m in result["data_schema"].schema if m.schema == CONF_SELECTED_K8S_WORKLOADS)
    field_selector = result["data_schema"].schema[marker]
    assert [str(option["value"]) for option in field_selector.config["options"]] == _ALL_KEYS
    assert result["data_schema"]({})[CONF_SELECTED_K8S_WORKLOADS] == _ALL_KEYS


async def test_full_setup_exposes_translated_kubernetes_entities(hass, config_entry) -> None:
    """End to end: a k3s-enabled host yields translated workload entities in the state machine."""
    config_entry.add_to_hass(hass)
    fake = _make_async_call([_K3S_SERVICE], _full_kubectl())

    async def async_call(service: str, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Serve the canned RPCs; anything else (update/apt checks) gets an empty list."""
        try:
            return await fake(service, method, params, **kwargs)
        except KeyError:
            return []

    with (
        patch(
            "custom_components.omv.OMVAPI.async_connect",
            new=AsyncMock(return_value={"hostname": "nas", "version": "8.1.2-1"}),
        ),
        patch("custom_components.omv.OMVAPI.async_call", new=AsyncMock(side_effect=async_call)),
        patch("custom_components.omv.OMVAPI.async_close", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Entity ids follow HA's device-name + translated-name scheme, like containers.
        state = hass.states.get("sensor.kubernetes_n8n_app_n8n_n8n_state")
        assert state is not None
        assert state.state == "running"
        assert state.attributes["options"] == list(K8S_WORKLOAD_STATES)
        assert state.attributes["friendly_name"] == "Kubernetes n8n-app/n8n n8n state"
        assert hass.states.get("sensor.kubernetes_n8n_app_n8n_n8n_cpu_usage").state == "0.15"
        memory = hass.states.get("sensor.kubernetes_n8n_app_n8n_n8n_memory_usage")
        assert memory.attributes["unit_of_measurement"] == "MiB"
        assert hass.states.get("sensor.kubernetes_db_app_db_db_cpu_usage").state == "unknown"
        assert hass.states.get("sensor.kubernetes_n8n_app_n8n_n8n_restarts").state == "82"
        assert hass.states.get("sensor.omv_nas_kubernetes_workloads_total").state == "8"
        button = hass.states.get("button.kubernetes_n8n_app_n8n_recreate_pods")
        assert button is not None
        assert button.attributes["friendly_name"] == "Kubernetes n8n-app/n8n Recreate pods"

        assert await hass.config_entries.async_unload(config_entry.entry_id)

---
applyTo: "custom_components/omv/coordinator.py"
---

# Coordinator — Conventions & Rules

## Responsibility boundary

`OMVDataUpdateCoordinator` owns **all** OMV-to-HA data fetching, normalisation, and filtering. Entities MUST NOT call `self.coordinator.api` directly. Every new data point is exposed through `coordinator.data[<key>]`.

## Adding a new data source (checklist)

1. **Fetch** in `_async_update_data` via one of the existing fetch helpers (see below). Never call `self.api.async_call` inline.
2. **Normalise** in a dedicated `_normalize_<name>` method that always returns a typed list or dict — never `Any` at the call site.
3. **Register** the result under a new key in `unfiltered_data`. Document the key in `copilot-instructions.md`.
4. **Filter** in `filter_data_by_selection` if the resource should appear in the options flow.
5. **Inventory** in `build_inventory` if users should be able to select/deselect it.

## Fetch helper selection

| Helper | When to use |
|--------|-------------|
| `_fetch_or_empty` | Core data that must always be present (disks, filesystems, services, network). Logs a warning on failure, returns `[]`. |
| `_fetch_optional` | Plugin-specific RPCs that may be absent on some OMV versions (compose, zfs, kvm). Logs at DEBUG only, returns `[]`. |
| `_fetch_optional_background_json` | RPCs that return an OMV background-task handle (e.g. `getVolumesBg`). Resolves the handle via `_async_read_exec_output` internally. |

Never add `try/except` inside `_async_update_data` to swallow errors — use the helpers above instead.

## Normalisation rules

- All normalisation methods take raw API payloads (`Any`) and return strongly typed Python objects.
- Call `_records_from_response(response)` to turn any OMV response shape (dict with `data`, list, or single dict) into `list[dict]`.
- Use `_coerce_float` / `_coerce_bool` / `_coerce_optional_float` for every field that may arrive as a string, `None`, or numeric. Never call `float()` or `int()` directly on API values.
- Size fields from OMV arrive as **bare numeric strings in bytes** (e.g. `"123456789"`). Use `_coerce_storage_gb` for display-ready GB values, or `_coerce_float` when bytes are needed downstream.
- Each normalised record must carry a stable `*_key` field (e.g. `disk_key`, `container_key`, `project_key`) that doubles as the unique identifier. The key is the value `filter_data_by_selection` and `build_inventory` use.

## Version-conditional logic

- Check `self.omv_version` (an `int`, major version only). Acceptable comparisons: `>= 7`, `>= 8`.
- New version branches must include a `_LOGGER.debug` explaining which path was taken.
- OMV 7 and 8 are the only actively supported targets. Do not add guards for versions < 7.

## Background-task pattern

When an RPC is known to return a background-task filename instead of records:

```python
# Step 1 — use the background-JSON helper
raw = await self._fetch_optional_background_json("Service", "getMethodBg", params)
# Step 2 — raw is already a parsed Python object (list/dict) or []
records = self._normalize_something(raw)
```

Do NOT re-implement polling logic (`_async_read_exec_output`) outside of `_fetch_optional_background_json`.

## Filtering contract

- `filter_data_by_selection(data, options)` MUST be **pure** (no `await`, no side-effects).
- The result must pass through every key that exists in `unfiltered_data`, even if unchanged (e.g. `filtered["kvm"] = list(data.get("kvm", []))`).
- Collection filtering always passes through when the user has made **no selection** (i.e. `_selected_values` returns `None`). Use `_filter_collection` for uniform behaviour.
- `compose_volumes` is always filtered by the set of container keys remaining after container/project filtering — never filter it independently.

## hwinfo caching

Hardware info is refreshed every `_HWINFO_REFRESH_MULTIPLIER` (60) update cycles, not every poll. Changes that add new hwinfo fields must handle the case where a cached `_hwinfo` from before the change is returned on the first N cycles. Default to `0` / `False` / `"unknown"` via `.get(key, default)` in `_normalize_hwinfo`.

## Network rate calculation

- Rates are computed as counter deltas between coordinator updates.
- On the **first update** after startup `_network_counters` has no previous entry → report `0.0` Mbps (never `None`).
- Always use `max(0.0, delta)` to guard against counter resets or reboots.
- Rates are in **Mbps** (bytes × 8 / seconds / 1 000 000). Do not change the unit without updating sensor unit definitions.

## SMART special cases

- Always pass `{"start": 0, "limit": 100}` to both `Smart.getListBg` and `Smart.getList`. Omitting `start` causes an OMV error.
- Skip `getAttributes` for device names starting with `mmcblk`, `sr`, or `bcache` — those are not SMART-capable.
- After fetching attributes, map them by `attrname` into the disk dict AND into `disk["smart_attributes"]` for entity access.

## Logging discipline

| Severity | When |
|----------|------|
| `_LOGGER.info` | Version detection at `async_init`, major one-time events. |
| `_LOGGER.warning` | `_fetch_or_empty` failures (core data unavailable). |
| `_LOGGER.debug` | All other: version branch decisions, optional RPC failures, background task output, compose command results. |

Never log with `_LOGGER.error` inside the coordinator — let `UpdateFailed` propagate to HA's error reporting.

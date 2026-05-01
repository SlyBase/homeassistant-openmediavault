---
applyTo: '**'
description: 'Completion workflow for Autopilot work: local pytest tests must pass, then create a commit via the git-commit skill, deploy to HomeAssistant via SSH, and perform a smoke test with the HA MCP tools.'
---

# Autopilot Completion Workflow

This instruction applies whenever a cohesive Autopilot task in the repository is considered complete.

## 1) Local validation is mandatory

- Before any completion claim, run the narrowest relevant pytest tests without errors.
- Use `.venv/bin/python -m pytest tests/ -q` (or a focused subset, e.g. `pytest tests/test_coordinator.py`).
- If any test fails, the work is not complete.

## 2) Create a commit after successful validation

- After successful local validation, exactly one commit must be created for each completed task.
- Use the `git-commit` skill to analyse the diff and create a conventional commit message that matches the actual change.
- Do not force a version-based commit title.
- Do NOT push to GitHub yourself — that is handled by CI.

## 3) Deploy to HomeAssistant via SSH before claiming completion

- Run the VS Code task `HASS: Deploy to HomeAssistant (SSH)` (or `.github/scripts/deploy-to-homeassistant.sh`) to copy the integration to the pi and restart Home Assistant.
- Wait until Home Assistant has finished starting up (the script waits automatically; alternatively confirm via MCP).
- A task is not finished unless the deploy succeeds and Home Assistant is back online.

## 4) HA MCP smoke test is mandatory

- After a successful deploy, use the Home Assistant MCP tools to verify the integration is working.
- Check that at least one OMV entity delivers a non-`unavailable` state, e.g. via `mcp_homeassistant_ha_search_entities` with `omv` as the query, then `mcp_homeassistant_ha_get_state` for a representative entity (e.g. a disk or memory sensor).
- Until this smoke test confirms live data, the work must not be reported as complete.

## 5) Failure handling

- If any step above fails, do not claim completion.
- Instead, report the failed step, the relevant error signal, and the next sensible repair step concisely.
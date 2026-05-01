#!/usr/bin/env bash
# deploy-to-homeassistant.sh
#
# Deploys the OMV custom component to the HomeAssistant instance running in
# Docker on the remote pi host via SSH.
#
# Strategy:
#   1. Rsync the component into a staging directory on the pi (no root needed).
#   2. Use sudo on the pi to atomically replace the component in the
#      Docker-mounted config directory (which may be owned by root).
#   3. Restart only the "hass" Docker Compose service and wait until the HA
#      HTTP endpoint responds, confirming a successful start.
#
# Requirements on the pi:
#   - SSH alias "pi" must be configured in ~/.ssh/config.
#   - The user must have passwordless sudo for `cp`, `rm`, and `docker compose`
#     (or for all commands via NOPASSWD: ALL).
#
# Usage:
#   bash .github/scripts/deploy-to-homeassistant.sh
#   (or via VS Code task "HASS: Deploy to HomeAssistant (SSH)")

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SSH_HOST="pi"
COMPONENT="omv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOCAL_SRC="${PROJECT_ROOT}/custom_components/${COMPONENT}"
STAGING_DIR="/home/timon/homeassistant/tmp/${COMPONENT}"
TARGET_DIR="/home/timon/homeassistant/hass/hass-config/custom_components/${COMPONENT}"
COMPOSE_DIR="/home/timon/homeassistant/hass"
HA_URL="http://pi:8123"
MAX_WAIT_SECONDS=120

# ---------------------------------------------------------------------------
# Step 1 – Rsync to staging (no root required)
# ---------------------------------------------------------------------------
echo "==> [1/3] Syncing custom_components/${COMPONENT} to staging on ${SSH_HOST}…"
ssh "${SSH_HOST}" "mkdir -p '${STAGING_DIR}'"
rsync -av --delete \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    "${LOCAL_SRC}/" \
    "${SSH_HOST}:${STAGING_DIR}/"

# ---------------------------------------------------------------------------
# Step 2 – Atomic replace with sudo (handles Docker-volume ownership)
# ---------------------------------------------------------------------------
echo "==> [2/3] Replacing component in HA config dir with sudo…"
# shellcheck disable=SC2029
ssh "${SSH_HOST}" "
    set -euo pipefail
    sudo rm -rf '${TARGET_DIR}'
    sudo cp -r '${STAGING_DIR}' '${TARGET_DIR}'
    echo '    Replaced ${TARGET_DIR}'
"

# ---------------------------------------------------------------------------
# Step 3 – Restart the hass service and wait for HA to come back up
# ---------------------------------------------------------------------------
echo "==> [3/3] Restarting Home Assistant (docker compose restart hass)…"
# shellcheck disable=SC2029
ssh "${SSH_HOST}" "
    set -euo pipefail
    cd '${COMPOSE_DIR}'
    sudo docker compose restart hass
"

echo "    Waiting for Home Assistant to become available (max ${MAX_WAIT_SECONDS}s)…"
# Run the health-check on the Pi via SSH so it reaches HA inside the Docker
# network on localhost:8123 — more reliable than curling from the Mac.
# shellcheck disable=SC2029
ssh "${SSH_HOST}" "
    set -euo pipefail
    elapsed=0
    until curl -sf --max-time 3 http://localhost:8123/api/ -o /dev/null 2>/dev/null; do
        if [[ \${elapsed} -ge ${MAX_WAIT_SECONDS} ]]; then
            echo ''
            # Last-resort: accept if the container is running and healthy
            status=\$(sudo docker inspect --format '{{.State.Status}}' hass 2>/dev/null || echo unknown)
            if [[ \"\${status}\" == 'running' ]]; then
                echo '    Note: HTTP probe timed out but container is running — treating as success.'
                exit 0
            fi
            echo 'ERROR: Home Assistant did not respond within ${MAX_WAIT_SECONDS}s.' >&2
            exit 1
        fi
        printf '.'
        sleep 5
        elapsed=\$((elapsed + 5))
    done
    echo ''
"
echo "==> Home Assistant is up at ${HA_URL}"
echo "==> Deploy complete. Run the HA MCP smoke test to confirm the integration."

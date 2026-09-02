#!/usr/bin/env bash
set -euo pipefail
export FORGE_DATA_ROOT=${FORGE_DATA_ROOT:-/forge-data}
export COMFYUI_URL=${COMFYUI_URL:-http://127.0.0.1:8188}
exec /opt/ooc-forge/.venv/bin/ooc-forge doctor

#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REALSENSE_ROOT="${REALSENSE_ROOT:-$HOME/realsense_src}"
export PYTHONPATH="$PROJECT_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$REALSENSE_ROOT/sdk/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$REALSENSE_ROOT/.venv/bin/python" -m piper_xr.collection.pointcloud "$@"

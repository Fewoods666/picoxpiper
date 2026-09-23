#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PIPER_PYTHON="${PIPER_PYTHON:-$HOME/anaconda3/envs/pico_teleop/bin/python}"
export PYTHONPATH="$PROJECT_ROOT"
exec "$PIPER_PYTHON" "$PROJECT_ROOT/scripts/restore_collection_can.py" "$@"

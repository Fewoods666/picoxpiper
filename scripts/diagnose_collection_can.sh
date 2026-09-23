#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PIPER_PYTHON="${PIPER_PYTHON:-$HOME/anaconda3/envs/pico_teleop/bin/python}"
export PYTHONPATH="$PROJECT_ROOT"
REPORT_NAME="can_diagnostic.json"
for arg in "$@"; do
  if [[ "$arg" == "--query-firmware" ]]; then
    REPORT_NAME="can_firmware_diagnostic.json"
  fi
done
exec "$PIPER_PYTHON" "$PROJECT_ROOT/scripts/diagnose_collection_can.py" \
  --output "$PROJECT_ROOT/work_dirs/collection/$REPORT_NAME" "$@"

#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT"
export PYTHONDONTWRITEBYTECODE=1
EVA_ROOT="${EVA_ROOT:-$HOME/EVA-CLIENT}"
exec "$EVA_ROOT/.venv/bin/python" -m piper_xr.collection.launch "$@"

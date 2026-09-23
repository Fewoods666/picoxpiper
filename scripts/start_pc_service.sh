#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="${XR_SERVICE_DIR:-/opt/apps/roboticsservice}"
CXX_RUNTIME="${XR_CXX_RUNTIME:-$HOME/anaconda3/envs/pico_teleop/lib/libstdc++.so.6}"

if [[ ! -x "$SERVICE_DIR/RoboticsServiceProcess" ]]; then
  echo "PC service executable not found: $SERVICE_DIR/RoboticsServiceProcess" >&2
  exit 1
fi
if [[ ! -f "$CXX_RUNTIME" ]]; then
  echo "C++ runtime not found; set XR_CXX_RUNTIME to libstdc++.so.6" >&2
  exit 1
fi

# Load only the required C++ runtime, avoiding unrelated Conda Qt libraries.
export LD_PRELOAD="$CXX_RUNTIME${LD_PRELOAD:+:$LD_PRELOAD}"
export LD_LIBRARY_PATH="$SERVICE_DIR:$SERVICE_DIR/lib:$SERVICE_DIR/SDK/x64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QT_PLUGIN_PATH="$SERVICE_DIR/plugins"
export QT_QML_PATH="$SERVICE_DIR/qml"
cd "$SERVICE_DIR"
exec ./RoboticsServiceProcess

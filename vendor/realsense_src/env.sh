#!/usr/bin/env bash
# Source this file in the terminal used for camera work.
REALSENSE_WORKSPACE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export REALSENSE_WORKSPACE
source "$REALSENSE_WORKSPACE/.venv/bin/activate"
export PATH="$REALSENSE_WORKSPACE/sdk/bin:$PATH"
export LD_LIBRARY_PATH="$REALSENSE_WORKSPACE/sdk/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CMAKE_PREFIX_PATH="$REALSENSE_WORKSPACE/sdk${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="$REALSENSE_WORKSPACE/sdk/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

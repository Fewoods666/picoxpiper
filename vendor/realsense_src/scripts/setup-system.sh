#!/usr/bin/env bash
set -euo pipefail
workspace="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $EUID -ne 0 ]]; then
    exec sudo /bin/bash "$workspace/scripts/setup-system.sh" "$@"
fi

rules="$workspace/librealsense/config/99-realsense-libusb.rules"
test -f "$rules"
destination=/etc/udev/rules.d/99-realsense-libusb.rules
if [[ -f "$destination" ]] && ! cmp -s "$rules" "$destination"; then
    cp -a "$destination" "$destination.backup-$(date +%Y%m%d-%H%M%S)"
fi
install -m 0644 "$rules" "$destination"
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=8086
udevadm trigger --subsystem-match=usb --attr-match=idVendor=38e5
printf '%s\n' 'RealSense USB rules installed. Unplug and reconnect the camera.'

if [[ "${1:-}" == "--with-tools" ]]; then
    apt-get update
    apt-get install -y --no-install-recommends v4l-utils ffmpeg python3-venv
fi

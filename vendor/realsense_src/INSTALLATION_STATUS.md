# Installation status

Verified on 2026-09-16, Ubuntu 22.04.5 LTS, x86_64, kernel 6.8.0-138-generic.

## Installed

- Official librealsense v2.58.4, source commit `34d6c778e1134d8505adcd56bb57acbe7598a459`.
- Local C++ SDK prefix: `/home/wtc/realsense_src/sdk`.
- Native build: Release, RSUSB ON, graphical tools ON, ROSBAG2 ON, DDS OFF, CUDA OFF.
- Viewer, depth-quality tool, recorder, converter, device enumerator, calibration examples and development headers.
- Python 3.10.12 environment: `/home/wtc/realsense_src/.venv`.
- Official PyPI pyrealsense2 2.58.4.10922 and packages listed in `requirements.lock.txt`.
- User application menu entry: `~/.local/share/applications/realsense-viewer.desktop`.
- Environment activation, USB setup, camera capture and offline validation scripts.

## Passed checks

- C++ SDK compiled successfully and local installation completed.
- Viewer reports version 2.58.4.0; shared libraries resolve.
- Viewer created an X11 window and remained running until the test closed it.
- Python dependency validation: `No broken requirements found`.
- Synthetic RGB-D recording/playback: 12 depth frames and 12 color frames.
- Recorded pixel values and camera timestamps preserved through `.db3` playback.
- SDK point-cloud calculation from replayed depth frames.
- 16-bit PNG depth round trip, including values up to 65535.
- HDF5 array and Open3D PLY point-cloud round trips.
- Bundled FFmpeg executable runs; OpenCV ArUco and hand-eye calibration APIs available.
- Shell/Python script syntax checks passed.

## Pending hardware and system steps

- No D435 found by USB enumeration, C++ SDK or Python SDK at verification time.
- Official udev rule absent from `/etc/udev/rules.d/99-realsense-libusb.rules`.
- Passwordless sudo is unavailable, so installation of system rules and optional apt tools was not executed.
- Run `bash /home/wtc/realsense_src/scripts/setup-system.sh --with-tools` in the local terminal.
- System `v4l-utils`, system FFmpeg and `python3-venv` are included in that pending command. The Python environment already has a working bundled FFmpeg.
- Then reconnect the camera and run `python scripts/camera.py check --require-camera --frames 150` after sourcing `env.sh`.
- USB 3 link negotiation, camera firmware compatibility, actual frames, long-duration throughput and physical calibration remain unverified.
- No firmware was flashed. ROS 2 was not installed.

The native SDK uses an embedded runtime library path; the installer's non-root `ldconfig` warning does not prevent these tools from loading the local libraries.

See `README.zh-CN.md` for connection, recording, timestamps, depth units and calibration guidance.

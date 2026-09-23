#!/usr/bin/env python3
"""D435 inspection, native RGB-D recording, and calibrated snapshots."""

import argparse
import csv
import importlib.metadata
import json
import signal
import sys
import time
from pathlib import Path

import numpy as np
import pyrealsense2 as rs


def device_info(device):
    fields = ("name", "serial_number", "firmware_version", "recommended_firmware_version",
              "usb_type_descriptor", "physical_port", "product_line")
    return {key: device.get_info(getattr(rs.camera_info, key)) for key in fields
            if device.supports(getattr(rs.camera_info, key))}


def intrinsics(profile):
    value = profile.as_video_stream_profile().get_intrinsics()
    return {"width": value.width, "height": value.height, "fx": value.fx, "fy": value.fy,
            "ppx": value.ppx, "ppy": value.ppy, "distortion_model": str(value.model),
            "coeffs": list(value.coeffs)}


def calibration(profile):
    depth = profile.get_stream(rs.stream.depth)
    color = profile.get_stream(rs.stream.color)
    extrinsics = depth.get_extrinsics_to(color)
    device = profile.get_device()
    sensors = []
    for sensor in device.query_sensors():
        options = {}
        for key in (rs.option.exposure, rs.option.gain, rs.option.enable_auto_exposure,
                    rs.option.emitter_enabled, rs.option.laser_power, rs.option.global_time_enabled):
            if sensor.supports(key):
                options[str(key)] = sensor.get_option(key)
        sensors.append({"name": sensor.get_info(rs.camera_info.name), "options": options})
    return {
        "schema_version": 1,
        "pyrealsense2_version": importlib.metadata.version("pyrealsense2"),
        "device": device_info(device),
        "depth_scale_meters": device.first_depth_sensor().get_depth_scale(),
        "depth_intrinsics": intrinsics(depth), "color_intrinsics": intrinsics(color),
        "depth_to_color": {"rotation_column_major": list(extrinsics.rotation),
                           "translation_meters": list(extrinsics.translation)},
        "fps": {"depth": depth.fps(), "color": color.fps()},
        "coordinate_convention": "optical: +X right, +Y down, +Z forward; meters",
        "sensors_at_start": sensors,
        "host_clock_note": "Host timestamps are receive times, not exposure times. "
                           "Camera timestamp domains are recorded separately.",
    }


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def choose_device(context, serial=None, allow_usb2=False):
    devices = [d for d in context.query_devices()
               if d.supports(rs.camera_info.serial_number)]
    if serial:
        devices = [d for d in devices if d.get_info(rs.camera_info.serial_number) == serial]
    if not devices:
        raise RuntimeError("No accessible RealSense camera. Connect the D435, install USB rules, "
                           "and close other camera applications.")
    if len(devices) != 1:
        raise RuntimeError("Multiple RealSense cameras found. Select one with --serial.")
    device = devices[0]
    info = device_info(device)
    print(json.dumps(info, indent=2))
    usb = info.get("usb_type_descriptor", "unknown")
    if not usb.startswith("3") and not allow_usb2:
        raise RuntimeError(f"USB 3 connection is required; reported USB version: {usb}. "
                           "Check the cable and port. --allow-usb2 overrides this check.")
    return device


def stream_config(device, args):
    config = rs.config()
    config.enable_device(device.get_info(rs.camera_info.serial_number))
    config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.rgb8, args.fps)
    return config


def frame_row(depth, color):
    return {
        "host_unix_ns": time.time_ns(), "host_monotonic_ns": time.monotonic_ns(),
        "depth_frame_number": depth.get_frame_number(),
        "color_frame_number": color.get_frame_number(),
        "depth_timestamp_ms": depth.get_timestamp(),
        "color_timestamp_ms": color.get_timestamp(),
        "depth_timestamp_domain": str(depth.get_frame_timestamp_domain()),
        "color_timestamp_domain": str(color.get_frame_timestamp_domain()),
    }


def check(args):
    print(f"Python: {sys.version.split()[0]}")
    for package in ("pyrealsense2", "numpy", "opencv-contrib-python", "open3d-cpu", "h5py",
                    "scipy", "matplotlib", "imageio-ffmpeg"):
        print(f"{package}: {importlib.metadata.version(package)}")
    rules = Path("/etc/udev/rules.d/99-realsense-libusb.rules")
    print(f"USB rules: {'installed' if rules.exists() else 'MISSING; run scripts/setup-system.sh'}")
    context = rs.context()
    devices = list(context.query_devices())
    print(f"RealSense devices: {len(devices)}")
    for device in devices:
        print(json.dumps(device_info(device), indent=2))
    if not devices:
        return 2 if args.require_camera or args.frames else 0
    if args.frames:
        device = choose_device(context, args.serial, args.allow_usb2)
        pipeline = rs.pipeline(context)
        pipeline.start(stream_config(device, args))
        try:
            start = time.monotonic()
            count = 0
            valid = []
            previous = None
            gaps = {"depth": 0, "color": 0}
            while count < args.frames:
                frames = pipeline.wait_for_frames(5000)
                depth, color = frames.get_depth_frame(), frames.get_color_frame()
                if not depth or not color:
                    if time.monotonic() - start > 10 + args.frames / args.fps * 3:
                        raise RuntimeError("Timed out waiting for paired RGB-D frames.")
                    continue
                row = frame_row(depth, color)
                if previous:
                    for name in gaps:
                        key = f"{name}_frame_number"
                        gaps[name] += max(0, row[key] - previous[key] - 1)
                previous = row
                pixels = np.asanyarray(depth.get_data())
                valid.append(float(np.count_nonzero(pixels) / pixels.size))
                count += 1
            print(json.dumps({"frames_received": count, "elapsed_seconds": time.monotonic() - start,
                              "mean_nonzero_depth_fraction": float(np.mean(valid)),
                              "observed_frame_number_gaps": gaps, "last_frame": previous}, indent=2))
        finally:
            pipeline.stop()
    return 0


def record(args):
    context = rs.context()
    device = choose_device(context, args.serial, args.allow_usb2)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = stream_config(device, args)
    config.enable_record_to_file(str(output / "recording.db3"))
    pipeline = rs.pipeline(context)
    profile = pipeline.start(config)
    count = 0
    started = time.monotonic()
    status = "failed"
    error = None
    previous = None
    gaps = {"depth": 0, "color": 0}
    try:
        write_json(output / "calibration.json", calibration(profile))
        print(f"Recording to {output}; Ctrl+C finishes the recording.", flush=True)
        with (output / "timestamps.csv").open("x", newline="", encoding="utf-8") as handle:
            writer = None
            while args.seconds == 0 or time.monotonic() - started < args.seconds:
                frames = pipeline.wait_for_frames(5000)
                depth, color = frames.get_depth_frame(), frames.get_color_frame()
                if not depth or not color:
                    continue
                row = frame_row(depth, color)
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                if previous:
                    for name in gaps:
                        key = f"{name}_frame_number"
                        gaps[name] += max(0, row[key] - previous[key] - 1)
                previous = row
                count += 1
                if count % args.fps == 0:
                    handle.flush()
            status = "completed"
    except KeyboardInterrupt:
        status = "interrupted_by_user"
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        pipeline.stop()
        write_json(output / "summary.json", {
            "status": status, "error": error, "observed_rgbd_pairs": count,
            "elapsed_seconds": time.monotonic() - started,
            "observed_frame_number_gaps": gaps,
            "note": "The SDK records original streams independently. CSV rows describe pairs "
                    "received by this process; counts are not a guarantee of lossless recording.",
        })
    if count == 0:
        raise RuntimeError("No paired RGB-D frames received; inspect the recording and connection.")
    print(f"Saved {count} observed RGB-D pairs and the SDK recording to {output}")
    return 0


def snapshot(args):
    import cv2

    context = rs.context()
    device = choose_device(context, args.serial, args.allow_usb2)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    pipeline = rs.pipeline(context)
    profile = pipeline.start(stream_config(device, args))
    try:
        for _ in range(30):
            frames = pipeline.wait_for_frames(5000)
        depth, color = frames.get_depth_frame(), frames.get_color_frame()
        if not depth or not color:
            raise RuntimeError("The snapshot requires both depth and color frames.")
        timestamps = frame_row(depth, color)
        aligned = rs.align(rs.stream.color).process(frames).get_depth_frame()
        if not aligned:
            raise RuntimeError("Depth-to-color alignment produced no frame.")
        rgb = np.asanyarray(color.get_data())
        images = {
            "color.png": cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
            "depth_raw.png": np.asanyarray(depth.get_data()),
            "depth_aligned_to_color.png": np.asanyarray(aligned.get_data()),
        }
        for name, image in images.items():
            if not cv2.imwrite(str(output / name), image):
                raise RuntimeError(f"Failed to write {name}")
        pointcloud = rs.pointcloud()
        pointcloud.map_to(color)
        pointcloud.calculate(depth).export_to_ply(str(output / "points.ply"), color)
        metadata = calibration(profile)
        metadata["timestamps"] = timestamps
        metadata["aligned_depth_intrinsics"] = intrinsics(aligned.profile)
        metadata["depth_encoding"] = "uint16 PNG; meters = pixel * depth_scale_meters; zero = invalid"
        metadata["pointcloud_frame"] = "native depth optical frame"
        write_json(output / "calibration.json", metadata)
    finally:
        pipeline.stop()
    print(f"Saved RGB, raw/aligned depth, calibration and point cloud to {output}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("check", check), ("record", record), ("snapshot", snapshot)):
        command = commands.add_parser(name)
        command.set_defaults(handler=handler)
        command.add_argument("--serial")
        command.add_argument("--width", type=int, default=640)
        command.add_argument("--height", type=int, default=480)
        command.add_argument("--fps", type=int, default=30)
        command.add_argument("--allow-usb2", action="store_true")
        if name == "check":
            command.add_argument("--require-camera", action="store_true")
            command.add_argument("--frames", type=int, default=0)
        else:
            command.add_argument("--output", type=Path, required=True,
                                 help="New output directory; existing directories are never overwritten.")
        if name == "record":
            command.add_argument("--seconds", type=float, default=60,
                                 help="Recording duration; 0 records until Ctrl+C.")
    args = parser.parse_args()
    if min(args.width, args.height, args.fps) <= 0:
        parser.error("Width, height and FPS must be positive.")
    if getattr(args, "seconds", 0) < 0 or getattr(args, "frames", 0) < 0:
        parser.error("Seconds and frame count must be nonnegative.")
    try:
        return args.handler(args)
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    def stop_on_signal(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_signal)
    sys.exit(main())

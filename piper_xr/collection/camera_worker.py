"""D435 worker launched with realsense_src/.venv/bin/python."""

import argparse
import importlib.util
import signal
import time
from multiprocessing.connection import Connection
from pathlib import Path

import numpy as np

from .ipc import send


def load_camera_module(root):
    path = Path(root) / "scripts" / "camera.py"
    spec = importlib.util.spec_from_file_location("piperxr_realsense_camera", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_frames(width, height, fps):
    intr = dict(width=width, height=height, fx=width, fy=width, ppx=width / 2,
                ppy=height / 2, distortion_model="distortion.none", coeffs=[0.0] * 5)
    calibration = dict(schema_version=1, synthetic=True, depth_scale_meters=0.001,
                       depth_intrinsics=intr, color_intrinsics=intr,
                       aligned_depth_intrinsics=intr,
                       depth_to_color=dict(rotation_column_major=np.eye(3).ravel().tolist(),
                                           translation_meters=[0, 0, 0]),
                       coordinate_convention="optical: +X right, +Y down, +Z forward; meters")
    y, x = np.indices((height, width))
    i = 0
    while True:
        i += 1
        color = np.stack(((x + i * 3) % 256, (y * 2) % 256, (x + y) % 256), axis=-1).astype("uint8")
        depth = (500 + x + y).astype("uint16")
        yield dict(t=time.monotonic(), host_unix_ns=time.time_ns(), frame_number=i,
                   color_frame_number=i, depth_timestamp_ms=i * 1000 / fps,
                   color_timestamp_ms=i * 1000 / fps, depth_timestamp_domain="synthetic",
                   color_timestamp_domain="synthetic", calibration=calibration), dict(
                       rgb=color, depth_raw=depth, depth_aligned=depth.copy())
        time.sleep(1 / fps)


def real_frames(args):
    camera = load_camera_module(args.realsense_root)
    rs = camera.rs
    context = rs.context()
    device = camera.choose_device(context, args.serial, False)
    pipeline = rs.pipeline(context)
    profile = pipeline.start(camera.stream_config(device, args))
    try:
        calibration = camera.calibration(profile)
        align = rs.align(rs.stream.color)
        for _ in range(30):
            pipeline.wait_for_frames(5000)
        while True:
            frames = pipeline.wait_for_frames(3000)
            received = time.monotonic()
            depth, color = frames.get_depth_frame(), frames.get_color_frame()
            if not depth or not color:
                raise RuntimeError("Missing paired RGB-D frame")
            metadata = camera.frame_row(depth, color)
            aligned = align.process(frames).get_depth_frame()
            if not aligned:
                raise RuntimeError("Depth alignment failed")
            calibration["aligned_depth_intrinsics"] = camera.intrinsics(aligned.profile)
            metadata.update(t=received, frame_number=depth.get_frame_number(), calibration=calibration)
            yield metadata, dict(rgb=np.asanyarray(color.get_data()).copy(),
                                 depth_raw=np.asanyarray(depth.get_data()).copy(),
                                 depth_aligned=np.asanyarray(aligned.get_data()).copy())
    finally:
        pipeline.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--realsense-root", default=str(Path.home() / "realsense_src"))
    parser.add_argument("--serial", default=None)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    connection = Connection(args.fd)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    frames = synthetic_frames(args.width, args.height, args.fps) if args.mock else real_frames(args)
    try:
        for metadata, arrays in frames:
            send(connection, metadata, arrays)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        frames.close()
        connection.close()


if __name__ == "__main__":
    main()

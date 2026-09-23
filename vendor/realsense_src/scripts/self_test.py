#!/usr/bin/env python3
"""Offline installation checks using synthetic data, without a physical camera."""

import gc
import json
import subprocess
import tempfile
from pathlib import Path

import cv2
import h5py
import imageio_ffmpeg
import numpy as np
import open3d as o3d
import pyrealsense2 as rs


def test_recording(directory):
    device = rs.software_device()
    device.register_info(rs.camera_info.name, "Offline synthetic RGB-D")
    device.register_info(rs.camera_info.serial_number, "offline-test")
    sensor = device.add_sensor("Synthetic RGB-D")
    sensor.add_read_only_option(rs.option.depth_units, 0.001)
    profiles = []
    for uid, (kind, fmt, bpp) in enumerate(((rs.stream.depth, rs.format.z16, 2),
                                          (rs.stream.color, rs.format.rgb8, 3))):
        intrinsic = rs.intrinsics()
        intrinsic.width, intrinsic.height = 64, 48
        intrinsic.fx, intrinsic.fy = 60, 60
        intrinsic.ppx, intrinsic.ppy = 32, 24
        intrinsic.model = rs.distortion.none
        stream = rs.video_stream()
        stream.type, stream.fmt, stream.bpp = kind, fmt, bpp
        stream.uid, stream.index, stream.fps = uid, 0, 30
        stream.width, stream.height, stream.intrinsics = 64, 48, intrinsic
        profiles.append(sensor.add_video_stream(stream).as_video_stream_profile())
    extrinsic = rs.extrinsics()
    extrinsic.rotation = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    extrinsic.translation = [0, 0, 0]
    profiles[0].register_extrinsics_to(profiles[1], extrinsic)
    filename = directory / "synthetic.db3"
    recorder = rs.recorder(str(filename), device)
    queue = rs.frame_queue(64)
    sensor.open(profiles)
    sensor.start(queue)
    buffers = []
    try:
        for number in range(1, 13):
            for profile, bpp, pixels in (
                (profiles[0], 2, np.full((48, 64), 1000 + number, dtype=np.uint16)),
                (profiles[1], 3, np.full((48, 64, 3), number, dtype=np.uint8)),
            ):
                buffers.append(pixels)
                frame = rs.software_video_frame()
                frame.pixels, frame.profile = pixels, profile
                frame.bpp, frame.stride = bpp, 64 * bpp
                frame.timestamp, frame.frame_number = 1000 + number * 1000 / 30, number
                frame.domain = rs.timestamp_domain.hardware_clock
                frame.depth_units = 0.001
                sensor.on_video_frame(frame)
                received = queue.wait_for_frame(2000)
                np.testing.assert_array_equal(np.asanyarray(received.get_data()), pixels)
    finally:
        sensor.stop()
        sensor.close()
        recorder.pause()
        del recorder
        gc.collect()
    assert filename.stat().st_size > 0

    context = rs.context()
    playback = context.load_device(str(filename))
    playback.set_real_time(False)
    replay_sensor = playback.query_sensors()[0]
    replay_queue = rs.frame_queue(64)
    replay_sensor.open(replay_sensor.get_stream_profiles())
    replay_sensor.start(replay_queue)
    seen = {rs.stream.depth: [], rs.stream.color: []}
    try:
        while True:
            success, frame = replay_queue.try_wait_for_frame(1000)
            if not success:
                break
            kind = frame.profile.stream_type()
            number = frame.get_frame_number()
            pixels = np.asanyarray(frame.get_data())
            assert np.all(pixels == (1000 + number if kind == rs.stream.depth else number))
            assert abs(frame.get_timestamp() - (1000 + number * 1000 / 30)) < 0.01
            seen[kind].append(number)
            if kind == rs.stream.depth:
                assert pixels.dtype == np.uint16
                points = rs.pointcloud().calculate(frame.as_depth_frame())
                assert points.size() == 64 * 48
    finally:
        replay_sensor.stop()
        replay_sensor.close()
    for kind, numbers in seen.items():
        assert numbers == list(range(1, 13)), (kind, numbers)
    return {"depth_frames": len(seen[rs.stream.depth]), "color_frames": len(seen[rs.stream.color]),
            "format": "db3", "pixel_and_timestamp_roundtrip": "passed"}


def main():
    with tempfile.TemporaryDirectory(prefix="realsense-offline-") as temporary:
        directory = Path(temporary)
        depth = np.array([[0, 1, 1000], [5000, 32768, 65535]], dtype=np.uint16)
        assert cv2.imwrite(str(directory / "depth.png"), depth)
        np.testing.assert_array_equal(cv2.imread(str(directory / "depth.png"), cv2.IMREAD_UNCHANGED), depth)
        with h5py.File(directory / "episode.h5", "w") as handle:
            handle.create_dataset("depth", data=depth)
        with h5py.File(directory / "episode.h5", "r") as handle:
            np.testing.assert_array_equal(handle["depth"][:], depth)
        cloud = o3d.geometry.PointCloud()
        cloud.points = o3d.utility.Vector3dVector([[0, 0, 1], [0.1, 0.2, 1.5]])
        assert o3d.io.write_point_cloud(str(directory / "points.ply"), cloud)
        restored = o3d.io.read_point_cloud(str(directory / "points.ply"))
        np.testing.assert_allclose(np.asarray(restored.points), np.asarray(cloud.points))
        assert hasattr(cv2, "aruco") and hasattr(cv2, "calibrateHandEye")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-version"], check=True, capture_output=True)
        result = {"synthetic_recording": test_recording(directory), "depth_png_uint16": "passed",
                  "hdf5": "passed", "pointcloud_ply": "passed", "ffmpeg": "passed",
                  "opencv_aruco_handeye": "available", "physical_camera": "not tested"}
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Export lossless RGB-D episode frames with the existing RealSense/Open3D runtime."""

import argparse
import json
from pathlib import Path

import cv2
import h5py
import numpy as np
import open3d as o3d
import pyrealsense2 as rs


def intrinsics(value):
    intr = rs.intrinsics()
    for field in ("width", "height", "fx", "fy", "ppx", "ppy", "coeffs"):
        setattr(intr, field, value[field])
    intr.model = getattr(rs.distortion, value["distortion_model"].split(".")[-1])
    return intr


def export(path, frame, output, stride=2, max_depth=2.0, voxel=0.005, denoise=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with h5py.File(path, "r") as source:
        calibration = json.loads(source.attrs["calibration"])
        rgb = source["rgb"][frame]
        raw = source["depth_raw"][frame]
        aligned = source["depth_aligned"][frame]
        capture = float(source["capture_time"][frame])
    for name, pixels in (("color.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)),
                         ("depth_raw.png", raw), ("depth_aligned_to_color.png", aligned)):
        if not cv2.imwrite(str(output / name), pixels):
            raise OSError(f"Failed to write {name}")
    depth_intr = intrinsics(calibration["depth_intrinsics"])
    color_intr = intrinsics(calibration["color_intrinsics"])
    extr = rs.extrinsics()
    extr.rotation = calibration["depth_to_color"]["rotation_column_major"]
    extr.translation = calibration["depth_to_color"]["translation_meters"]
    points, colors = [], []
    for y in range(0, raw.shape[0], stride):
        for x in range(0, raw.shape[1], stride):
            distance = float(raw[y, x]) * calibration["depth_scale_meters"]
            if not 0 < distance <= max_depth:
                continue
            point = rs.rs2_deproject_pixel_to_point(depth_intr, [x, y], distance)
            color_point = rs.rs2_transform_point_to_point(extr, point)
            if color_point[2] <= 0:
                continue
            uv = rs.rs2_project_point_to_pixel(color_intr, color_point)
            u, v = int(round(uv[0])), int(round(uv[1]))
            if 0 <= u < rgb.shape[1] and 0 <= v < rgb.shape[0]:
                points.append(point)
                colors.append(rgb[v, u] / 255.0)
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(np.asarray(points).reshape(-1, 3))
    cloud.colors = o3d.utility.Vector3dVector(np.asarray(colors).reshape(-1, 3))
    if voxel > 0:
        cloud = cloud.voxel_down_sample(voxel)
    if denoise and len(cloud.points) > 20:
        cloud, _ = cloud.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    if not len(cloud.points) or not o3d.io.write_point_cloud(str(output / "points.ply"), cloud):
        raise RuntimeError("No valid points or PLY writing failed")
    calibration.update(source=str(Path(path).resolve()), frame_index=frame, capture_time=capture,
                       pointcloud_frame="native depth optical frame", stride=stride,
                       max_depth_m=max_depth, voxel_m=voxel, statistical_outlier_filter=denoise)
    (output / "calibration.json").write_text(json.dumps(calibration, indent=2) + "\n")
    print(f"Saved {len(cloud.points)} points, RGB and uint16 depths to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--max-depth", type=float, default=2.0)
    parser.add_argument("--voxel", type=float, default=0.005)
    parser.add_argument("--denoise", action="store_true")
    args = parser.parse_args()
    if args.frame < 0 or args.stride < 1 or args.max_depth <= 0 or args.voxel < 0:
        parser.error("Invalid frame/stride/depth/voxel value")
    export(args.episode, args.frame, args.output, args.stride, args.max_depth, args.voxel, args.denoise)


if __name__ == "__main__":
    main()

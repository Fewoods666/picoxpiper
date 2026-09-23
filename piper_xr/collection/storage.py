"""Lossless RGB-D archives and exact links to EVA's saved collection episodes."""

import json
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np


class RGBDArchive:
    def __init__(self, directory, started, settings=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"capture_{uuid.uuid4().hex}.h5"
        self.partial = self.path.with_suffix(".partial.h5")
        self.started = started
        self.started_unix = time.time()
        self.settings = settings or {}
        self.finished = None
        self.accepted_times = []
        self.error = None
        self.queue = queue.Queue(maxsize=32)
        self.thread = threading.Thread(target=self._write, name="rgbd-archive", daemon=True)
        self.thread.start()

    def append(self, metadata, images, robot):
        if self.error:
            raise RuntimeError("RGB-D archive failed") from self.error
        try:
            self.queue.put_nowait((metadata, images, robot))
        except queue.Full as exc:
            raise RuntimeError("RGB-D disk writer cannot keep up; recording stopped") from exc

    def close(self, accepted_times=()):
        self.finished = time.monotonic()
        self.accepted_times = list(accepted_times)
        while self.thread.is_alive():
            try:
                self.queue.put(None, timeout=0.1)
                break
            except queue.Full:
                pass
        self.thread.join()
        if self.error:
            raise RuntimeError("RGB-D archive failed") from self.error
        return self.path

    def _write(self):
        try:
            with h5py.File(self.partial, "x") as out:
                out.attrs.update(schema_version=1, started=self.started,
                                 started_unix=self.started_unix,
                                 clock="host CLOCK_MONOTONIC receive time; not exposure time")
                out.attrs["settings"] = json.dumps(self.settings)
                out.attrs["state_layout"] = "joint1..joint6 [rad], gripper opening [m]"
                out.attrs["action_layout"] = "last issued absolute joint target [rad], gripper opening target [m]"
                out.attrs["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                count = 0
                while True:
                    item = self.queue.get()
                    if item is None:
                        break
                    meta, images, robot = item
                    if count == 0:
                        out.attrs["calibration"] = json.dumps(meta["calibration"])
                        out.attrs["synthetic"] = bool(meta["calibration"].get("synthetic", False))
                    values = dict(images, capture_time=np.float64(meta["t"]),
                                  robot_time=np.float64(robot[0]["t"]),
                                  state_time=np.float64(robot[0]["state_time"]),
                                  action_time=np.float64(robot[0]["action_time"]),
                                  state=robot[1]["state"], action=robot[1]["action"],
                                  depth_frame_number=np.int64(meta["frame_number"]),
                                  color_frame_number=np.int64(meta["color_frame_number"]),
                                  host_unix_ns=np.int64(meta["host_unix_ns"]),
                                  depth_timestamp_ms=np.float64(meta["depth_timestamp_ms"]),
                                  color_timestamp_ms=np.float64(meta["color_timestamp_ms"]))
                    for name, value in values.items():
                        value = np.asarray(value)
                        if name not in out:
                            out.create_dataset(name, shape=(0,) + value.shape,
                                               maxshape=(None,) + value.shape, dtype=value.dtype,
                                               chunks=(1,) + value.shape, compression="lzf")
                        dataset = out[name]
                        dataset.resize(count + 1, axis=0)
                        dataset[count] = value
                    for field in ("depth_timestamp_domain", "color_timestamp_domain"):
                        out.attrs[field] = meta[field]
                    count += 1
                out.create_dataset("eva_capture_time", data=np.asarray(self.accepted_times, dtype=float))
                out.attrs.update(frames=count, finished=self.finished,
                                 finished_unix=time.time(), complete=True)
                out.flush()
            self.partial.rename(self.path)
        except BaseException as exc:
            self.error = exc


def nearest_indices(source_times, target_times):
    source = np.asarray(source_times)
    target = np.asarray(target_times)
    if not len(source) or np.any(np.diff(source) <= 0):
        raise ValueError("RGB-D source timestamps must be nonempty and strictly increasing")
    right = np.searchsorted(source, target).clip(0, len(source) - 1)
    left = (right - 1).clip(0)
    # EVA's aligner chooses the earlier frame on equal distance.
    return np.where(np.abs(target - source[left]) <= np.abs(target - source[right]), left, right)


def link_episode(parquet, archives, tolerance=0.05):
    import pyarrow.parquet as pq

    parquet = Path(parquet)
    dataset = parquet.parents[2]
    destination = dataset / "rgbd" / parquet.parent.name / f"{parquet.stem}.h5"
    if destination.exists():
        return destination
    table = pq.read_table(parquet, columns=["capture_time", "frame_index"])
    target = np.asarray(table["capture_time"].to_pylist(), dtype=float)
    if not len(target):
        return None
    candidates = []
    # CLOCK_MONOTONIC restarts after reboot. EVA's episode wall date excludes
    # archives from a previous boot whose numerical monotonic range overlaps.
    wall_start = None
    metadata = dataset / "meta" / "episodes.jsonl"
    if metadata.exists():
        episode = int(parquet.stem.removeprefix("episode_"))
        for line in metadata.read_text().splitlines():
            row = json.loads(line)
            if row["episode_index"] == episode and row.get("started_at"):
                wall_start = datetime.fromisoformat(row["started_at"]).timestamp()
                break
    for path in archives:
        with h5py.File(path, "r") as source:
            if wall_start is not None and not (
                    source.attrs.get("started_unix", float("-inf")) - 2 <= wall_start
                    <= source.attrs.get("finished_unix", float("inf")) + 2):
                continue
            if (source.attrs.get("complete") and source.attrs.get("frames", 0)
                    and source.attrs["started"] <= target[0]
                    and source.attrs["finished"] >= target[-1]):
                candidates.append(path)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError(f"Ambiguous RGB-D archive for {parquet}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".partial.h5")
    with h5py.File(candidates[0], "r") as source:
        times = source["capture_time"][:]
        accepted = source["eva_capture_time"][:]
        eligible = np.flatnonzero(np.isin(times, accepted))
        if not len(eligible):
            raise ValueError(f"No EVA-consumed RGB-D frames found for {parquet}")
        indices = eligible[nearest_indices(times[eligible], target)]
        skew = times[indices] - target
        if np.max(np.abs(skew)) > tolerance:
            raise ValueError(f"RGB-D skew exceeds {tolerance}s for {parquet}")
        with h5py.File(partial, "w") as out:
            for key, value in source.attrs.items():
                out.attrs[key] = value
            out.attrs["source_archive"] = str(candidates[0])
            out.attrs["frames"] = len(target)
            out.attrs["parquet"] = str(parquet)
            out.create_dataset("capture_time", data=target)
            out.create_dataset("source_capture_time", data=times[indices])
            out.create_dataset("source_frame_index", data=indices)
            out.create_dataset("frame_index", data=table["frame_index"].to_pylist())
            out.create_dataset("image_skew_s", data=skew)
            for key, data in source.items():
                if key in {"capture_time", "eva_capture_time"}:
                    continue
                result = out.create_dataset(key, shape=(len(target),) + data.shape[1:],
                                            dtype=data.dtype, chunks=(1,) + data.shape[1:],
                                            compression="lzf")
                for i, index in enumerate(indices):
                    result[i] = data[index]
    partial.replace(destination)
    destination.with_suffix(".json").write_text(json.dumps({
        "schema_version": 1, "frames": len(target), "max_image_skew_s": float(np.abs(skew).max()),
        "source_archive": str(candidates[0]), "parquet": str(parquet),
        "depth_encoding": "uint16; meters = value * calibration.depth_scale_meters; 0 invalid",
        "pointcloud_frame": "native depth optical frame; no robot extrinsic applied",
    }, indent=2) + "\n")
    return destination


def link_dataset(root, tolerance=0.05):
    root = Path(root)
    archives = sorted((root / "raw_rgbd").glob("capture_*.h5"))
    linked = []
    # Collection datasets may be rooted directly at ``root`` or under a task
    # directory (EVA's collection writer uses ``<root>/<task>/raw``).
    candidates = list(root.glob("*/data/chunk-*/episode_*.parquet"))
    candidates += list(root.glob("*/raw/data/chunk-*/episode_*.parquet"))
    for parquet in sorted(set(candidates)):
        # The metadata row is committed after EVA has finished writing the video/table.
        metadata = parquet.parents[2] / "meta" / "episodes.jsonl"
        if not metadata.exists():
            continue
        episode = int(parquet.stem.removeprefix("episode_"))
        rows = [json.loads(line) for line in metadata.read_text().splitlines() if line.strip()]
        if not any(row["episode_index"] == episode for row in rows):
            continue
        result = link_episode(parquet, archives, tolerance)
        if result:
            linked.append(result)
    return linked

"""Run EVA's actual recorder/FK/video pipeline with deterministic RGB-D, no sockets."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import av
import h5py
import numpy as np
import pyarrow.parquet as pq

from piper_xr.collection.bridge import PROJECT, read_settings
from piper_xr.collection.camera_worker import synthetic_frames
from piper_xr.collection.launch import make_eva_config
from piper_xr.collection.storage import RGBDArchive, link_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = read_settings(PROJECT / "configs/collection_single_d435.json", mock=True)
    settings["output_dir"] = str(args.output.resolve())
    sys.path.insert(0, str(Path(settings["eva_root"]) / "src"))
    from piper_xr.collection.eva_plugin import SinglePiper, decode_collection_payload
    from core.config import load_config
    from core.recorder.episode import EpisodeLogger
    from core.types import RawCollectionSnapshot
    from openpi_client import msgpack_numpy

    config = load_config(make_eva_config(settings))
    logger = EpisodeLogger(args.output, SinglePiper(), 30, config.transport.dataset_keys,
                           convert_bgr_to_rgb=False, collection=config.collection,
                           gripper_open=0.05, gripper_close=0, gripper_threshold=None,
                           eef_reference_frame="base_link")
    packer = msgpack_numpy.Packer()
    task = settings["tasks"]["pick_place"][0][0]
    for episode in range(3):
        began = time.monotonic()
        archive = RGBDArchive(args.output / "raw_rgbd", began)
        logger.start_episode(task, collection_min_capture_time=began, collection_dataset="pick_place")
        frames = synthetic_frames(64, 48, 30)
        consumed = []
        for index in range(36):
            meta, images = next(frames)
            q = np.array([0.1 * np.sin(index / 20), 0.8, -0.8, 0, 0, 0, 0.03], dtype="float32")
            action = q.copy()
            action[0] += 0.005
            robot_meta = dict(t=meta["t"] + 0.002, state_time=meta["t"] - 0.002,
                              action_time=meta["t"] + 0.002)
            archive.append(meta, images, (robot_meta, dict(state=q, action=action)))
            # Some complete camera messages may not be selected by EVA; depth must use
            # exactly the same subset and nearest-neighbor rule as the RGB encoder.
            if index in (10, 20):
                continue
            consumed.append(meta["t"])
            payload = packer.pack(dict(t=meta["t"], images={"cam_high": images["rgb"]},
                                      state={"right_arm": q}, action=action, **{
                                          k: robot_meta[k] for k in ("state_time", "action_time")}))
            logger.ingest_collection_snapshot(RawCollectionSnapshot(
                timestamp=meta["t"], decode_raw=lambda value=payload: decode_collection_payload(value)))
        frames.close()
        archive.close(consumed)
        if episode == 1:
            logger.cancel_episode("synthetic cancel test")
        else:
            assert logger.end_episode()
    logger.finalize()
    linked = link_dataset(args.output)
    assert len(linked) == 2, linked
    summary = []
    for depth in linked:
        dataset = depth.parents[2]
        parquet = dataset / "data" / depth.parent.name / (depth.stem + ".parquet")
        video = dataset / "videos" / depth.parent.name / "observation.images.cam_high" / (depth.stem + ".mp4")
        table = pq.read_table(parquet)
        for column, dimension in (("observations.state.qpos", 7), ("action.qpos", 7),
                                  ("observations.state.eef", 8), ("action.eef", 8)):
            values = np.asarray(table[column].to_pylist())
            assert values.shape == (len(table), dimension) and np.isfinite(values).all()
        with av.open(video) as container:
            decoded = [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
        with h5py.File(depth) as source:
            assert source["depth_raw"].dtype == np.uint16
            assert source["depth_aligned"].dtype == np.uint16
            assert len(decoded) == len(table) == len(source["depth_raw"])
            assert np.allclose(source["capture_time"][:], table["capture_time"].to_pylist())
            # mp4 is lossy, but color layout and selected RGB frame must still agree.
            assert np.abs(np.asarray(decoded, dtype=float) - source["rgb"][:]).mean() < 5
            indices = source["source_frame_index"][:]
            assert 10 not in indices and 20 not in indices
        records = [json.loads(line) for line in (dataset / "meta/episodes.jsonl").read_text().splitlines()]
        record = next(row for row in records if row["episode_index"] == int(depth.stem.split("_")[1]))
        assert record["quality"] == "green", record
        summary.append(dict(episode=depth.stem, frames=len(table), quality=record["quality"],
                            rgbd=str(depth), video=str(video)))
    (args.output / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print("PASS: real EVA FK, LeRobot parquet/metadata, RGB mp4, uint16 depth, exact RGB-D frame links, cancel")


if __name__ == "__main__":
    main()

"""Exercise a running MOCK collector through EVA's real control channel."""

import argparse
import json
import time
from pathlib import Path

import av
import h5py
import numpy as np
import pyarrow.parquet as pq
import zmq


def request(endpoint, message):
    with zmq.Context.instance().socket(zmq.REQ) as sock:
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.RCVTIMEO, 5000)
        sock.connect(endpoint)
        sock.send_json(message)
        reply = sock.recv_json()
        if not reply.get("ok"):
            raise RuntimeError(reply)
        return reply


def wait_for(predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise TimeoutError("Collection integration condition did not become true")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control", default="tcp://127.0.0.1:5757")
    parser.add_argument("--bridge", default="tcp://127.0.0.1:5557")
    args = parser.parse_args()
    status = request(args.bridge, {"command": "status"})
    if not status.get("synthetic"):
        raise RuntimeError("This test is allowed only against synthetic workers")
    before = set(args.output.glob("*/raw/data/chunk-*/episode_*.parquet"))

    def command(value):
        return request(args.control, {"cmd": "web:" + value})

    command("tab_switch:collect")
    command("collect_arm:1")
    wait_for(lambda: request(args.bridge, {"command": "status"})["motion"])
    for cancel in (False, True, False):
        command("collect_start")
        wait_for(lambda: request(args.bridge, {"command": "status"})["recording"])
        time.sleep(2)
        command("collect_cancel" if cancel else "collect_stop")
        wait_for(lambda: not request(args.bridge, {"command": "status"})["recording"])
        time.sleep(0.5)
    command("collect_arm:0")
    wait_for(lambda: not request(args.bridge, {"command": "status"})["motion"])
    wait_for(lambda: len(set(args.output.glob("*/raw/data/chunk-*/episode_*.parquet")) - before) == 2)
    episodes = sorted(set(args.output.glob("*/raw/data/chunk-*/episode_*.parquet")) - before)
    for parquet in episodes:
        dataset = parquet.parents[2]
        depth = dataset / "rgbd" / parquet.parent.name / (parquet.stem + ".h5")
        wait_for(depth.exists)
        table = pq.read_table(parquet)
        frames = len(table)
        assert frames >= 10
        for key, size in (("observations.state.qpos", 7), ("action.qpos", 7),
                          ("observations.state.eef", 8), ("action.eef", 8)):
            values = np.asarray(table[key].to_pylist())
            assert values.shape == (frames, size) and np.isfinite(values).all()
        with h5py.File(depth) as source:
            assert source["depth_raw"].shape[0] == frames
            assert source["depth_raw"].dtype == np.uint16
            assert source["depth_aligned"].dtype == np.uint16
            assert np.allclose(source["capture_time"][:], table["capture_time"].to_pylist())
            assert np.max(np.abs(source["image_skew_s"][:])) <= 0.05
        video = dataset / "videos" / parquet.parent.name / "observation.images.cam_high" / (parquet.stem + ".mp4")
        with av.open(video) as container:
            assert sum(1 for _ in container.decode(video=0)) == frames
        metadata = [json.loads(line) for line in (dataset / "meta/episodes.jsonl").read_text().splitlines()]
        row = next(row for row in metadata if row["episode_index"] == int(parquet.stem.split("_")[1]))
        assert row["quality"] == "green", row
        print(f"PASS {parquet.name}: {frames} rows, 7D state/action, 8D EEF, RGB video, lossless aligned depths")
    print("PASS: save / cancel / save, motion gate, episode alignment and video counts")


if __name__ == "__main__":
    main()

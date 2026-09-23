"""Protocol, motion gating and lossless episode alignment, without hardware."""

import json
import time

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from piper_xr.collection.camera_worker import synthetic_frames
from piper_xr.collection.ipc import decode, encode
from piper_xr.collection.robot_worker import MotionLease
from piper_xr.collection.storage import RGBDArchive, link_episode, nearest_indices


def test_binary_protocol_preserves_depth_and_shape():
    arrays = dict(rgb=np.zeros((2, 3, 3), dtype="uint8"),
                  depth=np.array([[0, 65535, 1000]], dtype="uint16"),
                  state=np.arange(7, dtype="float32"))
    metadata, decoded = decode(encode(dict(t=4.5), arrays))
    assert metadata == {"t": 4.5}
    for name, value in arrays.items():
        assert np.array_equal(decoded[name], value)
        assert decoded[name].dtype == value.dtype
    with pytest.raises(ValueError):
        encode({}, {"bad": np.array([object()])})


def test_motion_is_locked_until_leased_and_expires():
    lease = MotionLease(0.6)
    assert not lease.active(10)
    lease.update({"motion": True}, 10)
    assert lease.active(10.5)
    assert not lease.active(10.7)
    lease.update({"motion": False}, 11)
    assert not lease.active(11.1)


def test_nearest_matches_eva_tie_and_rejects_bad_clock():
    assert nearest_indices([1, 2, 3], [0, 1.5, 2.8, 4]).tolist() == [0, 0, 2, 2]
    with pytest.raises(ValueError):
        nearest_indices([1, 1], [1])


def test_episode_depth_uses_only_eva_consumed_frames(tmp_path):
    start = time.monotonic()
    archive = RGBDArchive(tmp_path / "raw", start)
    generator = synthetic_frames(8, 6, 1000)
    times = []
    for i in range(5):
        meta, images = next(generator)
        times.append(meta["t"])
        robot = (dict(t=meta["t"], state_time=meta["t"], action_time=meta["t"]),
                 dict(state=np.zeros(7, dtype="float32"), action=np.ones(7, dtype="float32")))
        images["depth_raw"][0, 0] = 65535
        archive.append(meta, images, robot)
    generator.close()
    source = archive.close([times[0], times[4]])
    parquet = tmp_path / "dataset/data/chunk-000/episode_000000.parquet"
    parquet.parent.mkdir(parents=True)
    targets = [times[0], times[1], times[4]]
    pq.write_table(pa.table(dict(capture_time=targets, frame_index=[0, 1, 2])), parquet)
    sidecar = link_episode(parquet, [source])
    with h5py.File(sidecar) as data:
        assert data["depth_raw"].dtype == np.uint16
        assert data["depth_raw"][:, 0, 0].tolist() == [65535] * 3
        assert data["source_frame_index"][:].tolist() == [0, 0, 4]
        assert data["rgb"].shape == (3, 6, 8, 3)
        assert json.loads(data.attrs["calibration"])["depth_scale_meters"] == 0.001
        assert np.allclose(data["capture_time"][:], targets)
    assert link_episode(parquet, [source]) == sidecar


def test_bridge_record_lifecycle_and_no_heartbeat_rearm(tmp_path):
    from collections import deque
    from types import SimpleNamespace
    from piper_xr.collection.bridge import Bridge

    bridge = Bridge(dict(output_dir=str(tmp_path), mock=True))
    worker = SimpleNamespace(error=None, frames=deque([(dict(t=time.monotonic(), xr_healthy=True), {})]))
    bridge.camera = bridge.robot = worker
    with pytest.raises(RuntimeError):
        bridge.request(dict(command="record_start"))
    with pytest.raises(RuntimeError):
        bridge.request(dict(command="heartbeat", motion=True))
    bridge.request(dict(command="motion", enabled=True))
    first = bridge.request(dict(command="record_start"))
    second = bridge.request(dict(command="record_start"))
    assert first["started"] == second["started"]
    bridge.request(dict(command="record_stop", frame_times=[]))
    bridge.request(dict(command="record_stop"))
    assert bridge.archive is None
    assert len(list((tmp_path / "raw_rgbd").glob("capture_*.h5"))) == 1
    bridge.request(dict(command="motion", enabled=False))
    with pytest.raises(RuntimeError):
        bridge.request(dict(command="heartbeat", motion=True))

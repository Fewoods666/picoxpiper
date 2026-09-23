"""CAN freshness errors must identify the failing stream without accepting stale data."""

from types import SimpleNamespace

import numpy as np
import pytest

from piper_xr.collection.feedback import feedback_snapshot, read_feedback


def make_sdk(joint_stamp=1000.0, gripper_stamp=1000.0):
    joint_state = SimpleNamespace(**{f"joint_{i}": i * 1000 for i in range(1, 7)})
    joints = SimpleNamespace(time_stamp=joint_stamp, joint_state=joint_state, Hz=50.0)
    gripper = SimpleNamespace(time_stamp=gripper_stamp, Hz=50.0,
                              gripper_state=SimpleNamespace(grippers_angle=25000))
    return SimpleNamespace(GetArmJointMsgs=lambda: joints, GetArmGripperMsgs=lambda: gripper)


def test_fresh_stationary_feedback_keeps_radians_and_opening_in_meters():
    sdk = make_sdk()
    state = read_feedback(sdk, 0.05, now=1000.1)
    np.testing.assert_allclose(state[:6], np.deg2rad(np.arange(1, 7)))
    assert state[6] == pytest.approx(0.025)
    np.testing.assert_array_equal(read_feedback(sdk, 0.05, now=1000.2), state)


@pytest.mark.parametrize("stream,stamp,label", [
    ("joints", 0.0, "missing"),
    ("gripper", 0.0, "missing"),
    ("joints", 999.0, "stale"),
    ("gripper", 999.0, "stale"),
    ("joints", 1001.0, "future_timestamp"),
    ("gripper", float("nan"), "invalid_timestamp"),
])
def test_missing_stale_and_invalid_timestamps_rejected_with_stream_name(stream, stamp, label):
    sdk = make_sdk(joint_stamp=stamp) if stream == "joints" else make_sdk(gripper_stamp=stamp)
    with pytest.raises(RuntimeError, match=f"{stream}: {label}") as error:
        read_feedback(sdk, 0.05, now=1000.1)
    assert "0x2A8" in str(error.value) and "age=" in str(error.value)


def test_snapshot_copies_timestamp_before_reading_wall_clock(monkeypatch):
    from piper_xr.collection import feedback
    sdk = make_sdk()

    def clock():
        # Emulate the SDK updating its shared object just after time is sampled.
        sdk.GetArmJointMsgs().time_stamp = 1000.2
        sdk.GetArmGripperMsgs().time_stamp = 1000.2
        return 1000.1

    monkeypatch.setattr(feedback.time, "time", clock)
    _, streams = feedback_snapshot(sdk)
    assert all(item["status"] == "fresh" for item in streams.values())
    assert streams["joints"]["timestamp"] == 1000.0

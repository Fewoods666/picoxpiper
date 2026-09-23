"""Read SDK feedback with explicit, per-stream freshness diagnostics."""

import time

import numpy as np


FEEDBACK_IDS = {0x2A5: "joint1/2", 0x2A6: "joint3/4", 0x2A7: "joint5/6", 0x2A8: "gripper"}


def feedback_snapshot(sdk, max_age=0.3, now=None):
    # The SDK returns mutable objects updated by its receive thread. Copy the
    # timestamps before sampling wall time, so a later update cannot look future-dated.
    joints = sdk.GetArmJointMsgs()
    joint_stamp = float(joints.time_stamp)
    joint_hz = float(getattr(joints, "Hz", 0))
    joint_values = [float(getattr(joints.joint_state, f"joint_{i}")) for i in range(1, 7)]
    gripper = sdk.GetArmGripperMsgs()
    gripper_stamp = float(gripper.time_stamp)
    gripper_hz = float(getattr(gripper, "Hz", 0))
    width = float(gripper.gripper_state.grippers_angle) * 1e-6
    now = time.time() if now is None else now
    streams = {}
    for name, stamp, hz in (("joints", joint_stamp, joint_hz),
                            ("gripper", gripper_stamp, gripper_hz)):
        age = now - stamp
        status = ("missing" if stamp == 0 else "invalid_timestamp" if not np.isfinite(stamp)
                  else "future_timestamp" if age < 0 else "stale" if age >= max_age else "fresh")
        streams[name] = dict(timestamp=stamp if np.isfinite(stamp) else None,
                             age_s=age if stamp != 0 and np.isfinite(age) else None,
                             hz=hz if np.isfinite(hz) else None, status=status)
    return np.r_[np.deg2rad(np.asarray(joint_values) / 1000.0), width], streams


def describe_feedback(streams):
    descriptions = []
    for name, values in streams.items():
        age = values["age_s"]
        age_text = "unknown" if age is None else f"{age:.6f}s"
        descriptions.append(
            f"{name}: {values['status']}, timestamp={values['timestamp']}, "
            f"age={age_text}, SDK_Hz={values['hz']}"
        )
    return "; ".join(descriptions)


def read_feedback(sdk, gripper_open_m, max_age=0.3, now=None):
    state, streams = feedback_snapshot(sdk, max_age, now)
    if any(value["status"] != "fresh" for value in streams.values()):
        raise RuntimeError(
            f"PiPER CAN feedback is missing or stale (limit={max_age}s): {describe_feedback(streams)}. "
            "Expected joint feedback 0x2A5/0x2A6/0x2A7 and gripper 0x2A8. "
            "Run scripts/diagnose_collection_can.py with the Piper Python environment."
        )
    if not np.isfinite(state).all() or not -0.002 <= state[6] <= gripper_open_m + 0.02:
        raise RuntimeError(f"Invalid PiPER feedback or gripper stroke configuration: state={state.tolist()}")
    return state

"""PiPER worker. Only this process owns CAN and sends motion commands."""

import argparse
import signal
import time
from multiprocessing.connection import Connection

import numpy as np

from .ipc import receive, send
from .feedback import read_feedback


class MotionLease:
    def __init__(self, timeout=0.6):
        self.timeout = timeout
        self.deadline = 0.0
        self.enabled = False

    def update(self, message, now):
        self.enabled = bool(message.get("motion", False))
        self.deadline = now + self.timeout

    def active(self, now):
        return self.enabled and now < self.deadline


def build_controller(args):
    from piper_xr.config import R_HEADSET_TO_WORLD_PIPER, build_real_piper_config
    from piper_xr.paths import PIPER_URDF
    from piper_xr.real.piper_arm_proxy import PiperArmProxy
    from piper_xr.real.real_piper_teleop_controller import RealPiperTeleopController

    class CollectionProxy(PiperArmProxy):
        def connect(self):
            from piper_sdk import C_PiperInterface
            self._piper = C_PiperInterface(can_name=self.can_name, can_auto_init=False)
            # Disabling SDK auto-init leaves its CAN object unset. Open the
            # preconfigured SocketCAN interface explicitly before ConnectPort.
            # This checks the interface; it does not configure it or enable motors.
            self._piper.CreateCanBus(can_name=self.can_name, bustype="socketcan",
                                     expected_bitrate=1000000, judge_flag=True)
            self._piper.ConnectPort()
            self.last_sent = None
            self.enabled = False

        def enable(self):
            if self.enabled:
                return
            self._piper.EnableArm(7)
            status = self._piper.GetArmLowSpdInfoMsgs()
            if all(getattr(status, f"motor_{i}").foc_status.driver_enable_status
                   for i in range(1, 7)):
                self.enabled = True
                self._set_joint_mode()
            return self.enabled

        def feedback(self):
            return read_feedback(self._piper, args.gripper_open_m)

        def get_gripper_normalized(self):
            width = float(self._piper.GetArmGripperMsgs().gripper_state.grippers_angle) * 1e-6
            return float(np.clip(1 - width / args.gripper_open_m, 0, 1))

        def send_joint_angles_rad(self, q):
            super().send_joint_angles_rad(q)
            # Mirror the integer command conversion used by PiperArmProxy.
            self.last_sent = np.deg2rad((np.rad2deg(q) * 1000).astype(int) / 1000)

        def send_gripper_normalized(self, value):
            width_um = int(np.clip(1 - value, 0, 1) * args.gripper_open_m * 1e6)
            self._piper.GripperCtrl(width_um, args.gripper_effort, 0x01, 0x00)
            self.last_width = width_um * 1e-6

    class CollectionController(RealPiperTeleopController):
        def _topdown_anchor_quat(self, quat):
            # A collection clutch anchors the actual orientation without a preset rotation.
            return quat

    proxy = CollectionProxy(can_name=args.can, move_spd_rate=args.speed)
    ctrl = CollectionController(robot_urdf_path=PIPER_URDF,
                                manipulator_config=build_real_piper_config(hand=args.hand),
                                arm_proxies={f"{args.hand}_hand": proxy},
                                R_headset_world=R_HEADSET_TO_WORLD_PIPER,
                                scale_factor=args.scale, max_dq=args.max_dq)
    return ctrl, proxy


def reset_reference(ctrl, hand):
    for name in ("ref_ee_xyz", "ref_ee_quat", "ref_controller_xyz", "ref_controller_quat"):
        getattr(ctrl, name)[hand] = None
    ctrl.active[hand] = False


def run(args, connection):
    lease = MotionLease()
    ctrl, proxy = (None, None) if args.mock else build_controller(args)
    hand = f"{args.hand}_hand"
    q = np.array([0.0, 0.8, -0.8, 0.0, 0.0, 0.0, args.gripper_open_m])
    action = q.copy()
    xr_stamp, xr_seen = None, 0.0
    engaged = False
    began = time.monotonic()
    try:
        if proxy is not None:
            deadline = time.monotonic() + 3
            while True:
                try:
                    q = proxy.feedback()
                    break
                except RuntimeError as error:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"PiPER startup feedback unavailable after 3 seconds on {args.can}: {error}"
                        ) from error
                    time.sleep(0.02)
        while True:
            started = time.monotonic()
            while connection.poll():
                message, _ = receive(connection)
                lease.update(message, started)
            motion = lease.active(started)
            state_time = time.monotonic()
            if args.mock:
                if motion:
                    action[0] = 0.1 * np.sin(started - began)
                    action[6] = args.gripper_open_m * (0.5 + 0.5 * np.sin(started - began))
                q += (action - q) * 0.3
                healthy = True
            else:
                q = proxy.feedback()
                state_time = time.monotonic()
                ctrl._update_robot_state()
                stamp = ctrl.xr_client.get_timestamp_ns()
                if stamp and stamp != xr_stamp:
                    xr_stamp, xr_seen = stamp, started
                pose = np.asarray(ctrl.xr_client.get_pose_by_name(f"{args.hand}_controller"))
                healthy = bool(started - xr_seen < 0.25 and pose.shape == (7,)
                               and np.isfinite(pose).all() and np.linalg.norm(pose[3:]) > 0.5)
                grip = ctrl.xr_client.get_key_value_by_name(f"{args.hand}_grip") > 0.9
                moving = motion and healthy and grip
                if moving and not proxy.enabled:
                    proxy.enable()
                    moving = proxy.enabled
                if moving:
                    if not engaged:
                        reset_reference(ctrl, hand)
                        ctrl._last_cmd_q[hand] = q[:6].copy()
                    ctrl._update_ik()
                    if not np.isfinite(ctrl.placo_robot.state.q).all():
                        raise RuntimeError("IK produced non-finite joints")
                    ctrl._update_gripper_target()
                    ctrl._send_command()
                    action = np.r_[proxy.last_sent, proxy.last_width]
                elif engaged:
                    # Replace any outstanding target with the measured pose on clutch release.
                    proxy.send_joint_angles_rad(q[:6])
                    proxy.send_gripper_normalized(proxy.get_gripper_normalized())
                    action = np.r_[proxy.last_sent, proxy.last_width]
                    reset_reference(ctrl, hand)
                elif not proxy.enabled:
                    action = q.copy()
                engaged = moving
            sampled = time.monotonic()
            send(connection, dict(t=sampled, state_time=state_time, action_time=sampled,
                                  xr_healthy=healthy, motion=motion,
                                  engaged=engaged, synthetic=args.mock),
                 dict(state=q.astype("float32"), action=action.astype("float32")))
            time.sleep(max(0, 0.02 - (time.monotonic() - started)))
    finally:
        if proxy is not None:
            try:
                if proxy.enabled:
                    # Only issue a hold when current feedback is valid.
                    state = proxy.feedback()
                    proxy.send_joint_angles_rad(state[:6])
                    proxy.send_gripper_normalized(proxy.get_gripper_normalized())
            finally:
                close = getattr(proxy._piper, "DisconnectPort", None)
                if close:
                    close()
                ctrl.xr_client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--can", default="can0")
    parser.add_argument("--hand", choices=("left", "right"), default="right")
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--max-dq", type=float, default=0.02)
    parser.add_argument("--speed", type=int, default=20)
    parser.add_argument("--gripper-open-m", type=float, default=0.05)
    parser.add_argument("--gripper-effort", type=int, default=1000)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    connection = Connection(args.fd)
    try:
        run(args, connection)
    except (KeyboardInterrupt, EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


if __name__ == "__main__":
    main()

"""Join independent camera/robot workers and serve EVA's observation/RPC sockets."""

import argparse
import collections
import json
import logging
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Connection
from pathlib import Path

import zmq

from .ipc import receive, send
from .storage import RGBDArchive, link_dataset

LOG = logging.getLogger(__name__)
PROJECT = Path(__file__).resolve().parents[2]


def read_settings(path, mock=False):
    settings = json.loads(Path(path).read_text())
    for key in ("eva_root", "realsense_root", "piper_python", "output_dir"):
        value = Path(settings[key]).expanduser()
        settings[key] = str(value if value.is_absolute() else PROJECT / value)
    if mock:
        settings["output_dir"] += "_mock"
    settings["mock"] = mock
    if settings["fps"] <= 0 or settings["max_robot_skew_s"] <= 0:
        raise ValueError("FPS and synchronization tolerance must be positive")
    robot = settings["robot"]
    if not (0 < robot["gripper_open_m"] <= 0.1 and 1 <= robot["speed"] <= 100
            and 0 < robot["max_dq"] <= 0.08 and 0 < robot["scale"] <= 2
            and 0 < robot["gripper_effort"] <= 5000):
        raise ValueError("Invalid robot scale/speed/gripper/step settings")
    for name in ("obs_endpoint", "action_endpoint", "rpc_endpoint"):
        if not settings[name].startswith("tcp://127.0.0.1:"):
            raise ValueError("Collection sockets must bind to localhost")
    return settings


class Worker:
    def __init__(self, python, module, arguments, extra_env=None):
        parent, child = socket.socketpair()
        self.connection = Connection(parent.detach())
        env = dict(os.environ, PYTHONPATH=str(PROJECT), PYTHONUNBUFFERED="1")
        env.update(extra_env or {})
        try:
            self.process = subprocess.Popen(
                [str(python), "-m", module, "--fd", str(child.fileno()), *arguments],
                pass_fds=(child.fileno(),), env=env,
            )
        finally:
            child.close()
        self.camera = "camera_worker" in module
        self.frames = collections.deque(maxlen=1 if self.camera else 256)
        self.pending = queue.Queue(maxsize=16)
        self.error = None
        self.thread = threading.Thread(target=self._read, name=module, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while True:
                item = receive(self.connection)
                self.frames.append(item)
                if self.camera:
                    self.pending.put(item, timeout=2)
        except BaseException as error:
            self.error = error

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.connection.close()
        self.thread.join(timeout=1)


class Bridge:
    def __init__(self, settings):
        self.settings = settings
        self.workers = []
        self.archive = None
        self.motion = False
        self.lease_until = 0.0
        self.camera = None
        self.robot = None
        self.started = time.monotonic()
        self.frames = 0
        self.dropped = 0
        self.stop = threading.Event()
        self.link_error = None
        self.link_thread = None
        self.sockets = []

    def healthy(self):
        now = time.monotonic()
        return bool(self.camera and self.robot and self.camera.frames and self.robot.frames
                    and not self.camera.error and not self.robot.error
                    and now - self.camera.frames[-1][0]["t"] < 0.5
                    and now - self.robot.frames[-1][0]["t"] < 0.3)

    def request(self, message):
        command = message.get("command")
        if command == "status":
            return dict(ok=True, ready=self.healthy(), motion=self.motion,
                        synthetic=self.settings["mock"],
                        recording=self.archive is not None, frames=self.frames, dropped=self.dropped,
                        rgbd_link_error=self.link_error,
                        xr_healthy=bool(self.robot.frames and self.robot.frames[-1][0]["xr_healthy"]))
        if command == "heartbeat":
            # A heartbeat can extend an existing lease, never re-arm an expired one.
            if self.motion and message.get("motion") and self.healthy():
                self.lease_until = time.monotonic() + 0.7
            if message.get("motion") and not self.motion:
                raise RuntimeError("Motion is locked; switch EVA MOTION off and on to re-arm")
            return dict(ok=True)
        if command == "motion":
            enable = bool(message.get("enabled"))
            if enable and not self.healthy():
                raise RuntimeError("Camera/robot streams are not ready")
            if enable and not self.robot.frames[-1][0]["xr_healthy"]:
                raise RuntimeError("PICO input is missing or stale; connect XRoboToolkit first")
            self.motion = enable
            self.lease_until = time.monotonic() + 0.7 if enable else 0
            return dict(ok=True)
        if command == "record_start":
            if not self.healthy() or not self.motion:
                raise RuntimeError("Enable MOTION after camera, CAN and PICO are ready")
            if self.archive is None:
                self.archive = RGBDArchive(Path(self.settings["output_dir"]) / "raw_rgbd",
                                           time.monotonic(), self.settings)
                LOG.info("RGB-D recording: %s", self.archive.path)
            return dict(ok=True, started=self.archive.started)
        if command == "record_stop":
            if self.archive is not None:
                archive, self.archive = self.archive, None
                LOG.info("RGB-D saved: %s", archive.close(message.get("frame_times", [])))
            return dict(ok=True)
        raise ValueError(f"Unknown bridge command: {command}")

    def _link_loop(self):
        while not self.stop.wait(2):
            try:
                link_dataset(self.settings["output_dir"], self.settings["image_skew_tolerance_s"])
                self.link_error = None
            except Exception as error:
                self.link_error = str(error)
                LOG.exception("RGB-D episode linking failed; raw archives are retained")

    def run(self):
        settings = self.settings
        sys.path.insert(0, str(Path(settings["eva_root"]) / "src"))
        from openpi_client import msgpack_numpy
        packer = msgpack_numpy.Packer()
        context = zmq.Context.instance()
        pub = context.socket(zmq.PUB)
        rpc = context.socket(zmq.REP)
        unused_actions = context.socket(zmq.SUB)
        self.sockets = [pub, rpc, unused_actions]
        for sock in self.sockets:
            sock.setsockopt(zmq.LINGER, 0)
        pub.setsockopt(zmq.SNDHWM, 4)
        LOG.info("Binding observation endpoint: %s", settings["obs_endpoint"])
        pub.bind(settings["obs_endpoint"])
        LOG.info("Binding RPC endpoint: %s", settings["rpc_endpoint"])
        rpc.bind(settings["rpc_endpoint"])
        unused_actions.setsockopt(zmq.SUBSCRIBE, b"")
        LOG.info("Binding action endpoint: %s", settings["action_endpoint"])
        unused_actions.bind(settings["action_endpoint"])
        camera_args = ["--realsense-root", settings["realsense_root"], "--fps", str(settings["fps"])]
        for key in ("width", "height", "serial"):
            if settings["camera"].get(key):
                camera_args += ["--" + key, str(settings["camera"][key])]
        robot_args = []
        for key, value in settings["robot"].items():
            robot_args += ["--" + key.replace("_", "-"), str(value)]
        if settings["mock"]:
            camera_args.append("--mock")
            robot_args.append("--mock")
        camera_python = Path(settings["realsense_root"]) / ".venv/bin/python"
        LOG.info("Starting camera worker: python=%s, mock=%s, %sx%s @ %s FPS",
                 camera_python, settings["mock"], settings["camera"]["width"],
                 settings["camera"]["height"], settings["fps"])
        self.camera = Worker(camera_python, "piper_xr.collection.camera_worker", camera_args,
                             {"LD_LIBRARY_PATH": str(Path(settings["realsense_root"]) / "sdk/lib")})
        self.workers.append(self.camera)
        LOG.info("Starting robot worker: python=%s, mock=%s, CAN=%s, hand=%s; motion locked",
                 settings["piper_python"], settings["mock"], settings["robot"]["can"],
                 settings["robot"]["hand"])
        self.robot = Worker(settings["piper_python"], "piper_xr.collection.robot_worker", robot_args)
        self.workers.append(self.robot)
        self.link_thread = threading.Thread(target=self._link_loop, name="rgbd-linker", daemon=True)
        self.link_thread.start()
        last_gate = 0.0
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                if self.motion and (now >= self.lease_until or not self.healthy()
                                    or not self.robot.frames[-1][0]["xr_healthy"]):
                    self.motion = False
                    LOG.warning("Motion lease expired or source lost; robot clutch released")
                for worker in self.workers:
                    if worker.error or worker.process.poll() is not None:
                        name = "camera_worker" if worker.camera else "robot_worker"
                        raise RuntimeError(
                            f"{name} stopped (exit code: {worker.process.poll()}, "
                            f"IPC error: {worker.error!r}); see its preceding traceback"
                        ) from worker.error
                if now - last_gate >= 0.05:
                    send(self.robot.connection, dict(motion=self.motion))
                    last_gate = now
                if rpc.poll(1):
                    try:
                        reply = self.request(rpc.recv_json())
                    except Exception as error:
                        reply = dict(ok=False, error=str(error))
                    rpc.send_json(reply)
                try:
                    metadata, images = self.camera.pending.get_nowait()
                except queue.Empty:
                    time.sleep(0.001)
                    continue
                candidates = list(self.robot.frames)
                if not candidates:
                    continue
                robot = min(candidates, key=lambda item: abs(item[0]["t"] - metadata["t"]))
                skew = abs(robot[0]["t"] - metadata["t"])
                if skew > settings["max_robot_skew_s"]:
                    self.dropped += 1
                    if self.archive is not None:
                        raise RuntimeError(f"Robot/camera sample skew {skew:.3f}s exceeds limit")
                    continue
                if self.archive is not None and metadata["t"] > self.archive.started:
                    self.archive.append(metadata, images, robot)
                payload = dict(t=metadata["t"], images={"cam_high": images["rgb"]},
                               state={"right_arm": robot[1]["state"]}, action=robot[1]["action"],
                               robot_time=robot[0]["t"], state_time=robot[0]["state_time"],
                               action_time=robot[0]["action_time"])
                pub.send(packer.pack(payload))
                self.frames += 1
        finally:
            self.close()

    def close(self):
        self.stop.set()
        for worker in reversed(self.workers):
            worker.close()
        self.workers = []
        try:
            self.request(dict(command="record_stop"))
        finally:
            if self.link_thread:
                self.link_thread.join(timeout=30)
            for sock in self.sockets:
                sock.close()
            self.sockets = []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(read_settings(args.settings, args.mock))
    signal.signal(signal.SIGTERM, lambda *_: bridge.stop.set())
    signal.signal(signal.SIGINT, lambda *_: bridge.stop.set())
    try:
        bridge.run()
    finally:
        bridge.close()


if __name__ == "__main__":
    main()

"""EVA registry extensions, loaded by the local launcher before EVA starts."""

import threading

import numpy as np
import zmq
from openpi_client import msgpack_numpy

from core.registry import ROBOT_REGISTRY, TRANSPORT_REGISTRY
from core.types import CollectionRawBatch, CollectionRawSample, RawCollectionSnapshot
from robots.base import ActuatorGroup, CameraSpec, ObservationSchema, Robot, RobotVisConfig, VisPart
from robots.kinematics.pyroki import pyroki_arms
from robots.zoo.agilex_piper import AgilexPiper
from transport.zmq import ZmqTransport


@ROBOT_REGISTRY.register("single_piper_d435")
class SinglePiper(Robot):
    def __init__(self):
        super().__init__(
            name="single_piper_d435",
            actuator_groups=(ActuatorGroup("right_arm", 7,
                                           (*AgilexPiper.ARM_JOINTS, "gripper"), 6),),
            initial_qpos=np.zeros(7, dtype=np.float32),
            observation_schema=ObservationSchema((CameraSpec("front", "cam_high"),), ("right_arm",)),
            vis_config=RobotVisConfig(parts=(VisPart.from_segments(
                "right_arm", AgilexPiper.URDF, (0, 0, 0), (1, 0, 0, 0), 0, 7,
                [{"copy": [0, 6]}, {"gripper": 6, "range": [0, 0.07],
                                    "stroke": 0.035, "fingers": [1, -1]}]),)),
        )

    def build_kinematics(self, **kwargs):
        return pyroki_arms(AgilexPiper.URDF,
                          [{"joints": AgilexPiper.ARM_JOINTS, "eef_link": "gripper_base"}], **kwargs)


class PiperTransport(ZmqTransport):
    def __init__(self, config, robot):
        super().__init__(config, robot)
        self.endpoint = config.transport.rpc_endpoint
        self._heartbeat_stop = threading.Event()
        self._motion = False
        self._captured_times = []
        self._closed_local = False
        self._heartbeat_error = ""
        self._motion_lock = threading.Lock()
        self._heartbeat = threading.Thread(target=self._beat, name="piper-motion-lease", daemon=True)
        self._heartbeat.start()

    def rpc(self, command, **kwargs):
        # Each caller owns its socket; EVA calls transport hooks from different threads.
        with zmq.Context.instance().socket(zmq.REQ) as socket:
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.RCVTIMEO, 3000)
            socket.setsockopt(zmq.SNDTIMEO, 1000)
            socket.connect(self.endpoint)
            socket.send_json(dict(command=command, **kwargs))
            reply = socket.recv_json()
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "Piper collection bridge failed"))
        return reply

    def _beat(self):
        while not self._heartbeat_stop.wait(0.15):
            with self._motion_lock:
                try:
                    self.rpc("heartbeat", motion=self._motion)
                except Exception as error:
                    self._motion = False
                    self._heartbeat_error = str(error)

    def collection_diagnostics(self):
        return self._heartbeat_error or super().collection_diagnostics()

    def start_collection(self):
        with self._motion_lock:
            self.rpc("motion", enabled=True)
            self._motion = True
            self._heartbeat_error = ""

    def stop_collection(self):
        with self._motion_lock:
            self._motion = False
            self.rpc("motion", enabled=False)

    def start_policy_collection(self):
        raise RuntimeError("This configuration is for PICO collection only")

    def publish_action(self, action, target="real"):
        if target == "real":
            raise RuntimeError("Use PICO for motion; EVA HOME/policy commands are disabled in this configuration")

    def clear_collection_backlog(self):
        super().clear_collection_backlog()
        self._captured_times = []
        return self.rpc("record_start")["started"]

    def finish_collection_capture(self):
        try:
            self.rpc("record_stop", frame_times=self._captured_times)
            self._captured_times = []
        finally:
            super().finish_collection_capture()

    def acquire_collection_raw(self):
        reader = self._collection_reader
        payload = reader._drain_raw_collection()
        if payload is None:
            return None
        # Reuse EVA's disk journal; decoding must not hold full RGB arrays in memory.
        if reader._collection_journal is None:
            from transport.zmq import _WireCaptureJournal
            reader._collection_journal = _WireCaptureJournal(reader._collection_journal_dir)
        entry = reader._collection_journal.append(payload)
        from transport.zmq import _observation_timestamp
        timestamp = _observation_timestamp(payload)
        self._captured_times.append(timestamp)

        def decode():
            return decode_collection_payload(entry.read())

        return RawCollectionSnapshot(timestamp=timestamp, decode_raw=decode)

    def close(self):
        if self._closed_local:
            return
        self._closed_local = True
        self._heartbeat_stop.set()
        self._heartbeat.join(timeout=4)
        try:
            self.stop_collection()
            self.finish_collection_capture()
        finally:
            super().close()


@TRANSPORT_REGISTRY.register("piperxr")
def build_transport(config, robot):
    return PiperTransport(config, robot)


def decode_collection_payload(payload):
    raw = msgpack_numpy.unpackb(payload)
    batch = CollectionRawBatch()
    batch.images["cam_high"] = [CollectionRawSample(raw["t"], raw["images"]["cam_high"])]
    batch.vectors["state_qpos:right_arm"] = [CollectionRawSample(
        raw["state_time"], raw["state"]["right_arm"])]
    batch.vectors["action_qpos"] = [CollectionRawSample(raw["action_time"], raw["action"])]
    return batch

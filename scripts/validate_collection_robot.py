"""Validate SDK startup and the CAN adapter with no real CAN or XR connection."""

import importlib
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import can
import piper_sdk as installed_sdk

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
import _mock_piper_sdk
import _mock_xrobotoolkit_sdk

sys.modules["piper_sdk"] = _mock_piper_sdk
sys.modules["xrobotoolkit_sdk"] = _mock_xrobotoolkit_sdk

from piper_xr.collection.robot_worker import build_controller
from piper_xr.collection.feedback import read_feedback


def synthetic_feedback(stamp):
    for index, (first, second) in enumerate(((1000, 2000), (-3000, 4000), (5000, 6000))):
        data = first.to_bytes(4, "big", signed=True) + second.to_bytes(4, "big", signed=True)
        yield can.Message(arbitration_id=0x2A5 + index, data=data,
                          timestamp=stamp, is_extended_id=False)
    yield can.Message(arbitration_id=0x2A8, data=(25000).to_bytes(4, "big") + bytes(4),
                      timestamp=stamp, is_extended_id=False)


def validate_installed_sdk_startup(args):
    """Keep real SDK lifecycle checks; replace only CAN I/O and XR input."""
    sdk_class = installed_sdk.C_PiperInterface
    sdk_module = importlib.import_module(sdk_class.__module__)
    connect = sdk_class.ConnectPort
    events = []

    class OfflineCan:
        CAN_STATUS = sdk_module.C_STD_CAN.CAN_STATUS

        def __init__(self, channel, bustype, bitrate, judge, auto_init, callback):
            events.append(("create", channel, bustype, bitrate, judge, auto_init))

        def Init(self):
            events.append(("init",))
            return self.CAN_STATUS.INIT_CAN_BUS_OPENED_SUCCESS

        def SendCanMessage(self, can_id, data):
            events.append(("query", can_id, bytes(data)))
            return self.CAN_STATUS.SEND_MESSAGE_SUCCESS

        def Close(self):
            events.append(("close",))

    def connect_without_threads(instance):
        # Run the installed SDK's real ConnectPort and PiperInit against fake I/O.
        return connect(instance, start_thread=False)

    with patch.object(_mock_piper_sdk, "C_PiperInterface", sdk_class), \
            patch.object(sdk_module, "C_STD_CAN", OfflineCan), \
            patch.object(sdk_class, "ConnectPort", connect_without_threads), \
            patch.object(sdk_class, "EnableArm", side_effect=AssertionError("Unexpected enable")), \
            patch.object(sdk_class, "JointCtrl", side_effect=AssertionError("Unexpected motion")), \
            patch.object(sdk_class, "GripperCtrl", side_effect=AssertionError("Unexpected gripper")):
        controller, proxy = build_controller(args)
        try:
            assert proxy._piper.get_connect_status()
            assert not proxy.enabled
            assert events[0] == ("create", args.can, "socketcan", 1000000, True, False), events
            assert ("init",) in events
            assert ("query", 0x4AF, bytes([1, 0, 0, 0, 0, 0, 0, 0])) in events
            stamp = time.time()
            for frame in synthetic_feedback(stamp):
                proxy._piper.ParseCANFrame(frame)
            state = read_feedback(proxy._piper, args.gripper_open_m, now=stamp + 0.01)
            np.testing.assert_allclose(state[:6], np.deg2rad([1, 2, -3, 4, 5, 6]))
            assert np.isclose(state[6], 0.025)
            try:
                read_feedback(proxy._piper, args.gripper_open_m, now=stamp + 1)
            except RuntimeError as error:
                assert "joints: stale" in str(error) and "gripper: stale" in str(error)
            else:
                raise AssertionError("Real SDK stale feedback accepted")
        finally:
            proxy._piper.DisconnectPort()
            controller.xr_client.close()
    assert events[-1] == ("close",), events
    print("PASS: installed SDK CreateCanBus -> ConnectPort -> DisconnectPort; no motion/enable; CAN I/O and XR mocked")
    print("PASS: installed SDK decodes synthetic 0x2A5-0x2A8; units and Unix-second freshness verified")


def validate_passive_diagnostic():
    import json
    from scripts import diagnose_collection_can as diagnostic

    class ReceiveOnlyBus:
        def __init__(self, ids=(0x2A5, 0x2A6, 0x2A7, 0x2A8)):
            self.pending = []
            self.closed = False
            self.ids = ids

        def recv(self, timeout):
            if not self.pending:
                time.sleep(min(timeout, 0.01))
                self.pending = [frame for frame in synthetic_feedback(time.time())
                                if frame.arbitration_id in self.ids]
            return self.pending.pop(0) if self.pending else None

        def shutdown(self):
            self.closed = True

    sdk_class = installed_sdk.C_PiperInterface
    sdk_module = importlib.import_module(sdk_class.__module__)
    bus = ReceiveOnlyBus()
    with tempfile.TemporaryDirectory(prefix="piper_can_diagnostic_") as directory:
        output = Path(directory) / "report.json"
        active = dict(returncode=0, stdout="9: passive_mock_only: <NOARP,UP,LOWER_UP,ECHO> state UP\n"
                                           "link/can\n can state ERROR-ACTIVE\n bitrate 1000000\n")
        with patch.object(diagnostic, "C_PiperInterface", sdk_class), \
                patch.object(diagnostic, "interface_status", return_value=active), \
                patch.object(diagnostic.can, "Bus", return_value=bus), \
                patch.object(sdk_module, "C_STD_CAN", side_effect=AssertionError("Real SDK CAN opened")), \
                patch.object(sdk_class, "ConnectPort", side_effect=AssertionError("ConnectPort called")), \
                patch.object(sys, "argv", ["diagnose", "--can", "passive_mock_only", "--seconds", "0.09",
                                          "--output", str(output)]):
            assert diagnostic.main() == 0
        report = json.loads(output.read_text())
        assert report["passive"] and report["feedback_ready"] and bus.closed
        assert set(report["frames"]) == {"0x2A5", "0x2A6", "0x2A7", "0x2A8"}
        assert report["missing_expected_ids"] == []
    print("PASS: passive diagnostic JSON and receive-only flow, synthetic bus with real SDK decoder")
    for label, ids in (("empty", ()), ("no_gripper", (0x2A5, 0x2A6, 0x2A7)),
                       ("partial_joints", (0x2A5, 0x2A8))):
        sdk = sdk_class(can_name=f"passive_{label}_mock_only", can_auto_init=False)
        report = diagnostic.collect(ReceiveOnlyBus(ids), sdk, 0.025)
        assert not report["feedback_ready"], label
        assert set(report["missing_expected_ids"]) == {
            f"0x{key:03X}" for key in (0x2A5, 0x2A6, 0x2A7, 0x2A8) if key not in ids}
    print("PASS: passive diagnostic rejects empty bus, missing gripper and partial joint feedback")


def validate_firmware_query():
    from scripts import diagnose_collection_can as diagnostic

    class Clock:
        value = 0.0

        def now(self):
            return self.value

    class QueryBus:
        def __init__(self, clock, send_error=False, reply=True):
            self.clock = clock
            self.send_error = send_error
            self.reply = reply
            self.sent = []
            self.pending = []

        def send(self, message, timeout):
            # Allow precisely the SDK firmware read request, no control frames.
            assert message.arbitration_id == 0x4AF
            assert bytes(message.data) == bytes([1, 0, 0, 0, 0, 0, 0, 0])
            assert not message.is_extended_id and timeout == 0.2
            self.sent.append(message)
            if self.send_error:
                raise can.CanOperationError("No buffer space available", error_code=105)
            # Local echo must never be treated as a reply from the robot.
            self.pending.append(can.Message(arbitration_id=0x4AF, data=message.data,
                                            timestamp=time.time(), is_extended_id=False, is_rx=False))
            if self.reply:
                self.pending.append(can.Message(arbitration_id=0x4AF, data=b"S-V1.2.3",
                                                timestamp=time.time(), is_extended_id=False))

        def recv(self, timeout):
            self.clock.value += max(timeout, 0.001)
            return self.pending.pop(0) if self.pending else None

    for case, send_error, reply in (("response", False, True), ("silent", False, False),
                                     ("send_error", True, False)):
        clock = Clock()
        sdk = installed_sdk.C_PiperInterface(can_name=f"query_{case}_mock_only", can_auto_init=False)
        bus = QueryBus(clock, send_error, reply)
        with patch.object(diagnostic.time, "monotonic", clock.now):
            report = diagnostic.collect(bus, sdk, 5, query_firmware=True)
        assert len(bus.sent) == len(report["firmware_queries"]) == 3
        assert not report["feedback_ready"]
        if reply:
            assert report["firmware_version"] == "S-V1.2.3"
            assert report["frames"]["0x4AF"]["count"] == 3
            assert report["local_echo_frames"] == 3
        else:
            assert report["firmware_version"] is None and not report["frames"]
        assert all(item["queued"] is not send_error for item in report["firmware_queries"])
    print("PASS: firmware query matches installed SDK, max 3 requests, response/timeout/send-error handling, echo excluded")


def validate_interface_preflight():
    import json
    from scripts import diagnose_collection_can as diagnostic

    down = dict(returncode=0, stdout="10: can0: <NOARP,ECHO> state DOWN\n link/can\n can state STOPPED\n")
    up_text = "10: can0: <NOARP,UP,LOWER_UP,ECHO> state UP\n link/can\n can state ERROR-ACTIVE\n bitrate 1000000\n"
    diagnostic.require_active_interface(dict(returncode=0, stdout=up_text), "can0")
    for status, error_text in (
        (down, "DOWN/STOPPED"),
        (dict(returncode=0, stdout=up_text.replace("1000000", "500000")), "expected 1000000"),
        (dict(returncode=0, stdout=up_text.replace("ERROR-ACTIVE", "BUS-OFF")), "BUS-OFF"),
        (dict(returncode=1, stderr="Device does not exist"), "Cannot inspect"),
    ):
        try:
            diagnostic.require_active_interface(status, "can0")
        except RuntimeError as error:
            assert error_text in str(error), error
        else:
            raise AssertionError(f"Preflight accepted: {status}")
    with tempfile.TemporaryDirectory(prefix="piper_down_diagnostic_") as directory:
        output = Path(directory) / "report.json"
        with patch.object(diagnostic, "interface_status", return_value=down), \
                patch.object(diagnostic, "C_PiperInterface", side_effect=AssertionError("SDK initialized")), \
                patch.object(diagnostic.can, "Bus", side_effect=AssertionError("CAN opened")), \
                patch.object(sys, "argv", ["diagnose", "--query-firmware", "--output", str(output)]):
            assert diagnostic.main() == 1
        report = json.loads(output.read_text())
        assert not report["diagnostic_started"] and report["firmware_queries"] == []
        assert "DOWN/STOPPED" in report["error"] and "sudo ip link set can0 up" in report["error"]
    print("PASS: CAN preflight rejects DOWN/wrong bitrate/BUS-OFF/unavailable; DOWN CLI sends nothing and saves recovery commands")


def validate_output_role_restore():
    import json
    from scripts import restore_collection_can as recovery

    sdk_class = installed_sdk.C_PiperInterface
    sdk_module = importlib.import_module(sdk_class.__module__)
    encoded = []

    class EncodingOnlyCan:
        CAN_STATUS = sdk_module.C_STD_CAN.CAN_STATUS

        def __init__(self, *args):
            pass

        def Init(self):
            return self.CAN_STATUS.INIT_CAN_BUS_OPENED_SUCCESS

        def SendCanMessage(self, can_id, data):
            encoded.append((can_id, bytes(data)))
            return self.CAN_STATUS.SEND_MESSAGE_SUCCESS

    with patch.object(sdk_module, "C_STD_CAN", EncodingOnlyCan), \
            patch.object(sdk_class, "ConnectPort", side_effect=AssertionError("SDK connect forbidden")):
        sdk = sdk_class(can_name="role_encoding_mock_only", can_auto_init=False)
        sdk.CreateCanBus(can_name="role_encoding_mock_only")
        sdk.MasterSlaveConfig(0xFC, 0, 0, 0)
    request = recovery.output_role_request()
    assert encoded == [(request.arbitration_id, bytes(request.data))]
    assert encoded == [(0x470, bytes([0xFC, 0, 0, 0, 0, 0, 0, 0]))]
    assert not request.is_extended_id and not request.is_remote_frame

    with patch.object(recovery, "interface_status", side_effect=AssertionError("Preview inspected CAN")), \
            patch.object(recovery.can, "Bus", side_effect=AssertionError("Preview opened CAN")), \
            patch.object(sys, "argv", ["restore"]):
        assert recovery.main() == 0

    class OneShotBus:
        def __init__(self, fail=False):
            self.fail, self.sent, self.closed = fail, [], False

        def send(self, frame, timeout):
            assert (frame.arbitration_id, bytes(frame.data)) == encoded[0]
            assert timeout == 0.2
            self.sent.append(frame)
            if self.fail:
                raise can.CanOperationError("Simulated send failure")

        def shutdown(self):
            self.closed = True

    up = dict(returncode=0, stdout="5: can0: <NOARP,UP,ECHO> state UP\n"
                                  "link/can\n can state ERROR-ACTIVE\n bitrate 1000000\n")
    down = dict(returncode=0, stdout="5: can0: <NOARP,ECHO> state DOWN\n"
                                    "link/can\n can state STOPPED\n")
    with tempfile.TemporaryDirectory(prefix="piper_role_restore_") as directory:
        for case, status in (("queued", up), ("send_error", up), ("down", down)):
            bus = OneShotBus(fail=case == "send_error")
            output = Path(directory) / f"{case}.json"
            with patch.object(recovery, "interface_status", return_value=status), \
                    patch.object(recovery.can, "Bus", return_value=bus) as open_bus, \
                    patch.object(recovery.time, "sleep"), \
                    patch.object(sys, "argv", ["restore", "--apply", "--output", str(output)]):
                assert recovery.main() == (0 if case == "queued" else 1)
            report = json.loads(output.read_text())
            assert not report["configuration_verified"] and report["restart_required"]
            assert report["request"]["queued"] is (case == "queued")
            assert len(bus.sent) == (0 if case == "down" else 1)
            assert bus.closed is (case != "down")
            assert open_bus.call_count == (0 if case == "down" else 1)
        with patch.object(recovery.can, "Bus", side_effect=AssertionError("CAN opened")), \
                patch.object(recovery, "interface_status", side_effect=AssertionError("CAN inspected")), \
                patch.object(sys, "argv", ["restore", "--apply", "--output", str(output)]):
            assert recovery.main() == 1
    print("PASS: recovery payload matches real SDK; preview opens no CAN; apply sends only one 0x470; "
          "DOWN/output collision/send failure handled; device acceptance never assumed")


def main():
    args = SimpleNamespace(can="mock_only", speed=20, hand="right", scale=0.5,
                           max_dq=0.02, gripper_open_m=0.05, gripper_effort=1000)
    validate_installed_sdk_startup(args)
    validate_passive_diagnostic()
    validate_firmware_query()
    validate_interface_preflight()
    validate_output_role_restore()
    controller, proxy = build_controller(args)
    sdk = proxy._piper
    assert sdk.connected and not sdk.enabled
    sdk._joints.time_stamp = sdk._gripper.time_stamp = time.time()
    sdk._gripper.gripper_state.grippers_angle = 25000
    assert np.isclose(proxy.feedback()[6], 0.025)
    assert np.isclose(proxy.get_gripper_normalized(), 0.5)
    calls = []
    sdk.GripperCtrl = lambda *values: calls.append(values)
    proxy.send_gripper_normalized(1)
    assert calls[-1] == (0, 1000, 1, 0)
    proxy.send_gripper_normalized(0)
    assert calls[-1] == (50000, 1000, 1, 0)
    status = SimpleNamespace(**{
        f"motor_{i}": SimpleNamespace(foc_status=SimpleNamespace(driver_enable_status=True))
        for i in range(1, 7)})
    sdk.GetArmLowSpdInfoMsgs = lambda: status
    assert proxy.enable() and sdk.enabled
    hand = "right_hand"
    controller._last_cmd_q[hand] = np.zeros(6)
    controller.placo_robot.state.q[7:13] = np.array([0.5, 0.5, -0.5, 0, 0, 0])
    controller.gripper_pos_target[hand]["gripper"] = 0.5
    controller._send_command()
    assert np.max(np.abs(proxy.last_sent)) <= 0.020001
    assert np.isclose(proxy.last_width, 0.025)
    sdk._joints.time_stamp = 0
    try:
        proxy.feedback()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Stale CAN feedback must be rejected")
    controller.xr_client.close()
    print("PASS: connect without enable; SDK grippers_angle feedback; meter conversion; enabled gripper commands; bounded actual joint action; stale-feedback rejection")


if __name__ == "__main__":
    main()

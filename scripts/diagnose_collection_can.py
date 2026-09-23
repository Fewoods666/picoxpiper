"""Bounded CAN diagnosis: passive by default, optional firmware read query; no motion."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

import can
from piper_sdk import C_PiperInterface

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from piper_xr.collection.feedback import FEEDBACK_IDS, describe_feedback, feedback_snapshot


def interface_status(channel):
    try:
        result = subprocess.run(["ip", "-details", "-statistics", "link", "show", channel],
                                capture_output=True, text=True, timeout=3)
        return dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(error=str(error))


def require_active_interface(status, channel):
    if status.get("returncode") != 0:
        detail = status.get("error") or status.get("stderr") or "No interface status available"
        raise RuntimeError(f"Cannot inspect CAN interface {channel}: {detail}")
    output = status.get("stdout", "")
    flags = re.search(r"^\d+: [^\n]+?: <([^>]+)>", output)
    bitrate = re.search(r"\bbitrate\s+(\d+)", output)
    can_state = re.search(r"\bcan state ([A-Z-]+)", output)
    if not flags or "link/can" not in output:
        raise RuntimeError(f"{channel} is not a recognized CAN interface; check the interface name")
    quoted = shlex.quote(channel)
    recovery = (
        "Stop collection/controllers, then configure the intended interface in a local terminal:\n"
        f"sudo ip link set {quoted} down\n"
        f"sudo ip link set {quoted} type can bitrate 1000000\n"
        f"sudo ip link set {quoted} up"
    )
    if "UP" not in flags[1].split(",") or (can_state and can_state[1] == "STOPPED"):
        raise RuntimeError(f"CAN interface {channel} is DOWN/STOPPED; diagnosis not started.\n{recovery}")
    if not bitrate or int(bitrate[1]) != 1000000:
        actual = bitrate[1] if bitrate else "unset/unknown"
        raise RuntimeError(f"CAN interface {channel} bitrate is {actual}; expected 1000000.\n{recovery}")
    if can_state and can_state[1] == "BUS-OFF":
        raise RuntimeError(f"CAN interface {channel} is BUS-OFF; check the bus before recovery.\n{recovery}")


def collect(bus, sdk, seconds, query_firmware=False):
    counts = Counter()
    latest = {}
    errors = 0
    error_examples = []
    decode_errors = []
    query_attempts = []
    examples = {}
    local_echo_frames = 0
    start = time.monotonic()
    deadline = start + seconds
    next_report = start
    next_query = start
    while time.monotonic() < deadline:
        if query_firmware and len(query_attempts) < 3 and time.monotonic() >= next_query:
            # Exact read-only request from SDK SearchPiperFirmwareVersion.
            # A successful send only means the kernel queued it, not a device ACK.
            request = can.Message(arbitration_id=0x4AF, is_extended_id=False,
                                  data=[0x01, 0, 0, 0, 0, 0, 0, 0])
            attempt = dict(elapsed_s=time.monotonic() - start, can_id="0x4AF",
                           data=request.data.hex())
            try:
                bus.send(request, timeout=0.2)
                attempt["queued"] = True
            except (OSError, can.CanError) as error:
                attempt.update(queued=False, error=str(error))
            query_attempts.append(attempt)
            next_query = time.monotonic() + 1
        frame = bus.recv(timeout=min(0.1, max(0, deadline - time.monotonic())))
        if frame is not None:
            if frame.is_error_frame:
                errors += 1
                if len(error_examples) < 8:
                    error_examples.append(dict(can_id=f"0x{frame.arbitration_id:X}",
                                               data=frame.data.hex()))
            elif not frame.is_rx:
                local_echo_frames += 1
            elif not frame.is_extended_id and not frame.is_remote_frame:
                counts[frame.arbitration_id] += 1
                latest[frame.arbitration_id] = float(frame.timestamp)
                examples.setdefault(frame.arbitration_id, frame.data.hex())
                try:
                    sdk.ParseCANFrame(frame)
                except Exception as error:
                    if len(decode_errors) < 10:
                        decode_errors.append(f"0x{frame.arbitration_id:03X}: {error}")
        now = time.monotonic()
        if now >= next_report:
            _, streams = feedback_snapshot(sdk)
            print(describe_feedback(streams), flush=True)
            next_report = now + 1
    state, streams = feedback_snapshot(sdk)
    wall = time.time()
    missing = [f"0x{key:03X}" for key in FEEDBACK_IDS if not counts[key]]
    all_fresh = all(value["status"] == "fresh" for value in streams.values())
    ids_fresh = all(key in latest and 0 <= wall - latest[key] < 0.3 for key in FEEDBACK_IDS)
    version = sdk.GetPiperFirmwareVersion()
    return dict(
        duration_s=time.monotonic() - start, standard_data_frames=sum(counts.values()),
        error_frames=errors, error_examples=error_examples, local_echo_frames=local_echo_frames,
        decode_errors=decode_errors, streams=streams,
        firmware_queries=query_attempts, firmware_version=version if isinstance(version, str) else None,
        state=state.tolist() if all_fresh else None,
        missing_expected_ids=missing,
        feedback_ready=all_fresh and ids_fresh and not decode_errors,
        frames={f"0x{key:03X}": dict(count=count, label=FEEDBACK_IDS.get(key, "other"),
                                    latest_timestamp=latest[key], age_s=wall - latest[key],
                                    first_data_hex=examples[key])
                for key, count in sorted(counts.items())},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--can", default="can0")
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--query-firmware", action="store_true",
                        help="Send at most three SDK firmware read requests (0x4AF); no enable/mode/motion")
    args = parser.parse_args()
    if not 0 < args.seconds <= 60:
        parser.error("--seconds must be greater than 0 and at most 60")
    report = dict(channel=args.can, passive=not args.query_firmware,
                  mode="firmware_query" if args.query_firmware else "passive",
                  diagnostic_started=False, firmware_queries=[],
                  interface_before=interface_status(args.can))
    if args.query_firmware:
        print("Firmware query: at most three 0x4AF read requests; no enable/mode/motion commands.", flush=True)
    else:
        print("Passive CAN receive only; SDK ConnectPort/EnableArm and CAN send are not called.", flush=True)
    print(report["interface_before"].get("stdout") or report["interface_before"], flush=True)
    bus = None
    try:
        require_active_interface(report["interface_before"], args.can)
        # Construct only the decoder and feedback cache. No SDK CAN connection or threads.
        sdk = C_PiperInterface(can_name=args.can, can_auto_init=False)
        bus = can.Bus(interface="socketcan", channel=args.can, receive_own_messages=False,
                      ignore_rx_error_frames=False, local_loopback=False)
        report["diagnostic_started"] = True
        report.update(collect(bus, sdk, args.seconds, query_firmware=args.query_firmware))
    except (OSError, can.CanError, ValueError, RuntimeError) as error:
        report.update(error=str(error), feedback_ready=False)
    finally:
        if bus is not None:
            bus.shutdown()
        report["interface_after"] = interface_status(args.can)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            print(f"Report saved: {args.output.resolve()}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report.get("error"):
        print(f"CAN diagnostic error: {report['error']}")
    elif args.query_firmware:
        if report.get("firmware_version") and set(report.get("frames", {})) == {"0x4AF"}:
            print(f"Controller answered firmware query: {report['firmware_version']}; "
                  "only 0x4AF replies received, no periodic state feedback.")
            print("固件查询收发已通过，但关节/夹爪周期反馈仍缺失，数采尚不能启动。")
            print("请按手册第 12.4 节停止控制程序、妥善支撑机械臂并重启本体电源，"
                  "启动完成后运行被动诊断；仅重启 can0 不会重启机械臂控制器。")
        elif report.get("frames", {}).get("0x4AF"):
            print("Firmware response frames received; inspect firmware_version and joint/gripper feedback.")
        else:
            print("No firmware response received in this window. Queued requests alone do not prove delivery.")
    return 0 if report.get("feedback_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())

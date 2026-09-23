"""Explicit one-shot PiPER motion-output role configuration; preview by default."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import can

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from scripts.diagnose_collection_can import interface_status, require_active_interface


def output_role_request():
    # Matches SDK MasterSlaveConfig(0xFC, 0, 0, 0) and piper_set_slave.py.
    return can.Message(arbitration_id=0x470, is_extended_id=False,
                       data=[0xFC, 0, 0, 0, 0, 0, 0, 0], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--can", default="can0")
    parser.add_argument("--apply", action="store_true",
                        help="Write the motion-output role and default CAN offsets once; reboot afterward")
    parser.add_argument("--output", type=Path, help="New report file; existing files are not overwritten")
    args = parser.parse_args()
    request = output_role_request()
    print(f"Channel: {args.can}; SDK equivalent: MasterSlaveConfig(0xFC, 0, 0, 0)")
    print(f"One configuration frame: 0x{request.arbitration_id:03X}#{request.data.hex().upper()}")
    print("Sets motion-output (follower) role and restores default feedback/control/linkage CAN IDs.")
    print("This changes the robot role. Stop controllers and support the stationary arm before applying.")
    print("No motor enable/disable, joint/gripper target, zeroing or factory reset commands are sent.")
    if not args.apply:
        print("PREVIEW ONLY: no interface opened and no frames sent. Use --apply to write this configuration.")
        return 0

    output = args.output or PROJECT / "work_dirs/collection" / (
        f"can_role_restore_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.json")
    report = dict(channel=args.can, mode="restore_motion_output", passive=False,
                  sdk_equivalent="MasterSlaveConfig(0xFC, 0, 0, 0)",
                  request=dict(can_id="0x470", data=request.data.hex(), attempted=False, queued=False),
                  configuration_verified=False, restart_required=True)
    # Reserve a new report before CAN access so a bad output path cannot hide a write.
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        log = output.open("x", encoding="utf-8")
    except OSError as error:
        print(f"Cannot create report; no CAN access attempted: {error}", file=sys.stderr)
        return 1
    bus = None
    with log:
        try:
            report["interface_before"] = interface_status(args.can)
            require_active_interface(report["interface_before"], args.can)
            bus = can.Bus(interface="socketcan", channel=args.can,
                          receive_own_messages=False, local_loopback=False)
            report["request"]["attempted"] = True
            bus.send(request, timeout=0.2)
            report["request"]["queued"] = True
            # Leave the socket open briefly for transmission; this is not an ACK.
            time.sleep(1)
        except (OSError, can.CanError, ValueError, RuntimeError, KeyboardInterrupt) as error:
            report["error"] = str(error) or type(error).__name__
        finally:
            if bus is not None:
                try:
                    bus.shutdown()
                except (OSError, can.CanError) as error:
                    report["error"] = f"CAN close failed: {error}"
            report["interface_after"] = interface_status(args.can)
            json.dump(report, log, indent=2)
            log.write("\n")
    print(json.dumps(report, indent=2))
    print(f"Report saved: {output.resolve()}")
    if report.get("error"):
        print(f"Configuration error: {report['error']}")
        print("Configuration attempt failed or was interrupted. No automatic retry was made.")
        return 1
    print("QUEUED ONLY: device acceptance and restored feedback have NOT been verified.")
    print("Exit other controllers, power-cycle the supported arm, and wait for startup to finish.")
    print("Then run: bash ~/picoxpiper/PiperXR-master/scripts/diagnose_collection_can.sh --seconds 10")
    print("Require fresh 0x2A5-0x2A8 feedback before restarting collection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

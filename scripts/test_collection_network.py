"""Bounded localhost-only mock test; launch synthetic workers, validate, shut down."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import zmq

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((PROJECT / "configs/collection_single_d435.json").read_text())
    config.update(output_dir=str(args.output / "data"), web_port=18080, control_port=15757,
                  obs_endpoint="tcp://127.0.0.1:15555", action_endpoint="tcp://127.0.0.1:15556",
                  rpc_endpoint="tcp://127.0.0.1:15557")
    settings = args.output / "settings.json"
    settings.write_text(json.dumps(config, indent=2) + "\n")
    env = dict(os.environ, PYTHONPATH=str(PROJECT), PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    logs = (args.output / "runtime.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "piper_xr.collection.launch", "mock", "--settings", str(settings)],
        env=env, cwd=PROJECT, stdout=logs, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Mock launch failed; see {logs.name}")
            with zmq.Context.instance().socket(zmq.REQ) as sock:
                sock.setsockopt(zmq.LINGER, 0)
                sock.setsockopt(zmq.RCVTIMEO, 500)
                sock.connect("tcp://127.0.0.1:15757")
                sock.send_json({"query": "status"})
                try:
                    if sock.recv_json().get("ok"):
                        break
                except zmq.Again:
                    pass
            time.sleep(0.2)
        else:
            raise TimeoutError("Mock EVA control channel startup timed out")
        subprocess.run(
            [sys.executable, str(PROJECT / "scripts/test_collection_flow.py"),
             "--output", str(args.output / "data_mock"),
             "--control", "tcp://127.0.0.1:15757", "--bridge", "tcp://127.0.0.1:15557"],
            cwd=PROJECT, env=env, check=True, timeout=100)
    finally:
        process.send_signal(signal.SIGTERM) if process.poll() is None else None
        try:
            process.wait(timeout=55)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        logs.close()
    print(f"Network integration test passed; log: {logs.name}")


if __name__ == "__main__":
    main()

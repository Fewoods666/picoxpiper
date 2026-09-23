"""Configure and supervise a single-PiPER/D435 EVA collection session."""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import zmq

from .bridge import PROJECT, read_settings
from .diagnostics import ProcessLog
from .storage import link_dataset


def make_eva_config(settings):
    output = Path(settings["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    config = dict(
        _base_=[str(Path(settings["eva_root"]) / "configs/00_base/defaults.py")],
        work_dir=str(output / "eva_runtime"),
        robot=dict(type="single_piper_d435", gripper_threshold=None,
                   gripper_open=settings["robot"]["gripper_open_m"], gripper_close=0.0),
        transport=dict(type="piperxr", sub_endpoint=settings["obs_endpoint"],
                       pub_endpoint=settings["action_endpoint"], rpc_endpoint=settings["rpc_endpoint"],
                       convert_bgr_to_rgb=False, image_layout="hwc", resize_pad=False,
                       image_width=settings["camera"]["width"], image_height=settings["camera"]["height"]),
        console=dict(initial_tab="collect"),
        policy=dict(type="mock"),
        control_channel=dict(enabled=True, host="127.0.0.1", port=settings["control_port"]),
        collection=dict(
            storage=dict(log_dir=str(output), fps=settings["fps"], save_queue_max=4,
                         image_skew_tolerance_sec=settings["image_skew_tolerance_s"]),
            schema=dict(robot_type="single_piper_d435", min_episode_frames=10,
                        arms=dict(right_arm="right"),
                        cameras=dict(cam_high="observation.images.cam_high"),
                        columns=dict(qpos="observations.state.qpos", eef="observations.state.eef",
                                     action_qpos="action.qpos", action_eef="action.eef")),
            teleop=dict(control_source="transport"), tasks=settings["tasks"]),
        inference_cfg=dict(publish_rate=settings["fps"], setup_warmup_chunks=0),
    )
    path = output / "eva_single_piper_d435.py"
    path.write_text("\n".join(f"{key} = {value!r}\n" for key, value in config.items()))
    return path


def rpc(settings, command="status", **kwargs):
    with zmq.Context.instance().socket(zmq.REQ) as sock:
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.RCVTIMEO, 1000)
        sock.setsockopt(zmq.SNDTIMEO, 1000)
        sock.connect(settings["rpc_endpoint"])
        sock.send_json(dict(command=command, **kwargs))
        return sock.recv_json()


def preflight(settings):
    checks = [
        (settings["piper_python"], "import numpy, placo, piper_sdk, xrobotoolkit_sdk; print('Piper runtime OK')"),
        (str(Path(settings["realsense_root"]) / ".venv/bin/python"),
         "import numpy, h5py, cv2, pyrealsense2, open3d; print('RGB-D runtime OK')"),
    ]
    for python, code in checks:
        subprocess.run([python, "-c", code], check=True, env=dict(os.environ, PYTHONPATH=str(PROJECT)))
    sys.path.insert(0, str(Path(settings["eva_root"]) / "src"))
    from . import eva_plugin  # noqa: F401
    from core.config import load_config
    config = make_eva_config(settings)
    loaded = load_config(config)
    print(f"EVA configuration OK: {config}; robot={loaded.robot.type}")
    print("Configuration check only; camera/CAN/PICO and motion were not started.")


def stop_process(process, interrupt=False):
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT if interrupt else signal.SIGTERM)
    try:
        process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run(settings, settings_path, headless=False, start_pc_service=False):
    config = make_eva_config(settings)
    env = dict(os.environ, EVA_ROOT=settings["eva_root"], PYTHONPATH=str(PROJECT),
               PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    bridge = eva = pc = None
    bridge_log = None
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        if start_pc_service and not settings["mock"]:
            pc = subprocess.Popen(["bash", str(PROJECT / "scripts/start_pc_service.sh")],
                                  start_new_session=True)
        command = [sys.executable, "-m", "piper_xr.collection.bridge", "--settings",
                   str(Path(settings_path).expanduser().resolve())]
        if settings["mock"]:
            command.append("--mock")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = Path(settings["output_dir"]) / "logs" / f"bridge_{stamp}_{os.getpid()}.log"
        bridge_log = ProcessLog(log_path)
        print(f"Bridge/camera/robot log: {log_path}", flush=True)
        bridge = subprocess.Popen(command, env=env, cwd=PROJECT, start_new_session=True,
                                  stdout=bridge_log.output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 45
        while not stopping and time.monotonic() < deadline:
            if bridge.poll() is not None:
                raise bridge_log.failure("Collection bridge failed during startup", bridge)
            try:
                if rpc(settings).get("ready"):
                    break
            except zmq.ZMQError:
                pass
            time.sleep(0.2)
        else:
            if stopping:
                return
            raise bridge_log.failure("Camera/robot startup timed out after 45 seconds", bridge)
        command = [sys.executable, "-m", "piper_xr.collection.eva_entry", "--config", str(config),
                   "--web-port", str(settings["web_port"])]
        if headless:
            command.append("--headless")
        eva = subprocess.Popen(command, env=env, cwd=PROJECT, start_new_session=True)
        print(f"EVA: http://127.0.0.1:{settings['web_port']}", flush=True)
        print(f"Dataset: {settings['output_dir']}; motion is initially LOCKED", flush=True)
        while not stopping and eva.poll() is None:
            if bridge.poll() is not None:
                raise bridge_log.failure("Collection bridge stopped; EVA is shutting down", bridge)
            time.sleep(0.2)
        if eva.poll() not in (None, 0):
            raise RuntimeError(f"EVA exited with code {eva.returncode}")
    finally:
        if bridge is not None and bridge.poll() is None:
            try:
                rpc(settings, "motion", enabled=False)
            except zmq.ZMQError:
                pass
        try:
            stop_process(eva, interrupt=True)
            stop_process(bridge)
            stop_process(pc)
        finally:
            if bridge_log is not None:
                bridge_log.close()
        # A post-save association failure must not hide the original startup error.
        active_error = sys.exc_info()[0] is not None
        try:
            linked = link_dataset(settings["output_dir"], settings["image_skew_tolerance_s"])
            print(f"RGB-D episode sidecars ready: {len(linked)}", flush=True)
        except Exception as error:
            if not active_error:
                raise
            print(f"Additional RGB-D linking error (raw archives retained): {error}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "mock", "real", "link"))
    parser.add_argument("--settings", type=Path, default=PROJECT / "configs/collection_single_d435.json")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--start-pc-service", action="store_true")
    parser.add_argument("--mock-data", action="store_true", help="Use the mock output directory for check/link")
    args = parser.parse_args()
    if args.mode == "real" and args.mock_data:
        parser.error("--mock-data cannot be used with real mode")
    settings = read_settings(args.settings, mock=args.mode == "mock" or args.mock_data)
    if args.mode == "check":
        preflight(settings)
    elif args.mode == "link":
        print("\n".join(map(str, link_dataset(settings["output_dir"], settings["image_skew_tolerance_s"]))))
    else:
        run(settings, args.settings, args.headless, args.start_pc_service)


if __name__ == "__main__":
    main()

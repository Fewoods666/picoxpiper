"""Startup failures must preserve the original child error without hardware."""

import subprocess
import sys
from pathlib import Path

import pytest
import zmq

from piper_xr.collection import launch
from piper_xr.collection.diagnostics import ProcessLog


def test_worker_stderr_is_saved_and_included_in_failure(tmp_path, capsys):
    log = ProcessLog(tmp_path / "logs" / "bridge.log")
    worker_code = "raise ValueError('CreateCanBus must run before ConnectPort: 初始化失败')"
    bridge_code = (
        "import subprocess, sys; "
        f"subprocess.run([sys.executable, '-c', {worker_code!r}], check=True)"
    )
    try:
        with subprocess.Popen([sys.executable, "-c", bridge_code], stdout=log.output,
                              stderr=subprocess.STDOUT) as process:
            process.wait(timeout=10)
            error = log.failure("Bridge startup failed", process)
    finally:
        log.close()
    assert "CreateCanBus must run before ConnectPort: 初始化失败" in log.path.read_text()
    assert "CreateCanBus must run before ConnectPort: 初始化失败" in str(error)
    assert "exit code: 1" in str(error)
    assert str(log.path) in str(error)
    assert "初始化失败" in capsys.readouterr().out


def test_launch_preserves_bridge_error_when_final_linking_also_fails(tmp_path, monkeypatch):
    settings_path = launch.PROJECT / "configs/collection_single_d435.json"
    settings = launch.read_settings(settings_path, mock=True)
    settings["output_dir"] = str(tmp_path)
    popen = subprocess.Popen
    children = []

    def start_failed_bridge(command, **kwargs):
        assert command[2] == "piper_xr.collection.bridge"
        assert Path(command[4]).is_absolute()
        process = popen([sys.executable, "-c", "raise ValueError('Missing CAN bus object')"], **kwargs)
        children.append(process)
        return process

    def unavailable_rpc(*args, **kwargs):
        raise zmq.Again()

    def bad_link(*args):
        raise ValueError("Unrelated old archive problem")

    monkeypatch.setattr(launch.subprocess, "Popen", start_failed_bridge)
    monkeypatch.setattr(launch, "rpc", unavailable_rpc)
    monkeypatch.setattr(launch, "link_dataset", bad_link)
    monkeypatch.setattr(launch.signal, "signal", lambda *args: None)
    with pytest.raises(RuntimeError, match="Missing CAN bus object"):
        launch.run(settings, settings_path)
    assert len(children) == 1 and children[0].poll() == 1
    logs = list((tmp_path / "logs").glob("bridge_*.log"))
    assert len(logs) == 1 and "Missing CAN bus object" in logs[0].read_text()

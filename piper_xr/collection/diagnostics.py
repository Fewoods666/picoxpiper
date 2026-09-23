"""Persist subprocess output while keeping it visible in the launch terminal."""

import sys
import threading


class ProcessLog:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Children write straight to disk, so a stopped terminal cannot block them.
        self.output = path.open("xb", buffering=0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._relay, name="collection-log", daemon=True)
        self._thread.start()

    def _relay(self):
        terminal = sys.stdout
        with self.path.open(encoding="utf-8", errors="replace") as source:
            while True:
                stopping = self._stop.is_set()
                text = source.read(65536)
                if text:
                    if terminal is not None:
                        try:
                            terminal.write(text)
                            terminal.flush()
                        except (OSError, ValueError):
                            terminal = None
                    continue
                if stopping:
                    return
                self._stop.wait(0.1)

    def failure(self, message, process):
        with self.path.open("rb") as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - 16384))
            tail = source.read().decode("utf-8", errors="replace")
        tail = "\n".join(tail.splitlines()[-100:]) or "(No subprocess output was captured.)"
        return RuntimeError(
            f"{message} (exit code: {process.poll()}).\n"
            f"Full bridge/camera/robot log: {self.path}\n"
            f"Last subprocess output:\n{tail}"
        )

    def close(self):
        self.output.close()
        self._stop.set()
        self._thread.join(timeout=2)

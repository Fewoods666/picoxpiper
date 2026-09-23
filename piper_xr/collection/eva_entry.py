"""Register local extensions, then enter the installed EVA application."""

import os
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(os.environ["EVA_ROOT"]) / "src"))
    from . import eva_plugin  # noqa: F401
    from main import main as eva_main
    try:
        eva_main()
    except KeyboardInterrupt:
        # EVA's run() executes its own finally block before propagating Ctrl+C.
        pass


if __name__ == "__main__":
    main()

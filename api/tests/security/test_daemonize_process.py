from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
DAEMONIZER = ROOT / "scripts/daemonize_process.py"


def test_daemon_survives_launcher_exit_and_uses_private_files(tmp_path: Path) -> None:
    pid_file = tmp_path / "service.pid"
    log_file = tmp_path / "service.log"
    subprocess.run(
        [
            sys.executable,
            str(DAEMONIZER),
            "--pid-file",
            str(pid_file),
            "--log-file",
            str(log_file),
            "--",
            sys.executable,
            "-c",
            "import time; print('ready', flush=True); time.sleep(30)",
        ],
        check=True,
    )
    pid = int(pid_file.read_text())
    try:
        for _ in range(100):
            if log_file.exists() and "ready" in log_file.read_text():
                break
            time.sleep(0.01)
        else:
            raise AssertionError("daemon did not become ready")
        os.kill(pid, 0)
        assert pid_file.stat().st_mode & 0o777 == 0o600
        assert log_file.stat().st_mode & 0o777 == 0o600
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

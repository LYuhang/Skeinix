"""CLI smoke: --version + dump-openapi work without starting uvicorn."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_cli_version():
    r = subprocess.run(
        [sys.executable, "-m", "vibecanvas_api.cli", "--version"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "vibecanvas-api" in r.stdout


def test_dump_openapi(tmp_path: Path):
    out = tmp_path / "openapi.json"
    r = subprocess.run(
        [sys.executable, "-m", "vibecanvas_api.cli", "dump-openapi",
         "--output", str(out)],
        capture_output=True, text=True, timeout=90,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert out.is_file()
    spec = json.loads(out.read_text())
    # T2 added /healthz; later tasks add more paths. At minimum:
    assert "openapi" in spec
    assert "paths" in spec
    assert "/healthz" in spec["paths"]

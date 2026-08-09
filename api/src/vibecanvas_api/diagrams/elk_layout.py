"""Bounded bridge to the project-pinned Eclipse Layout Kernel runtime."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from .limits import MAX_SCENE_BYTES, DiagramLimitError

ELK_LAYOUT_VERSION = "elkjs-0.12.0"
ELK_LAYOUT_TIMEOUT_SECONDS = 4.0


def _default_script() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "web"
        / "scripts"
        / "elk-layout.mjs"
    )


@lru_cache(maxsize=256)
def _run_cached(payload: bytes) -> bytes:
    node = os.getenv("VIBECANVAS_NODE_BIN") or shutil.which("node")
    script = Path(
        os.getenv("VIBECANVAS_ELK_LAYOUT_SCRIPT") or _default_script()
    )
    if node is None or not script.is_file():
        raise DiagramLimitError(
            "layout_engine_unavailable",
            "The pinned ELK layout runtime is unavailable. Install web "
            "dependencies and ensure Node.js is on PATH.",
        )
    try:
        completed = subprocess.run(
            [
                node,
                "--max-old-space-size=192",
                "--disable-proto=throw",
                str(script),
            ],
            input=payload,
            capture_output=True,
            check=False,
            timeout=ELK_LAYOUT_TIMEOUT_SECONDS,
            cwd=script.parent.parent,
            env={
                "PATH": os.environ.get("PATH", ""),
                "NODE_NO_WARNINGS": "1",
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise DiagramLimitError(
            "layout_timeout",
            "ELK layout exceeded its isolated 4-second time limit.",
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()[:500]
        raise DiagramLimitError(
            "layout_engine_failed",
            f"ELK layout failed in the isolated worker: {detail}",
        )
    if len(completed.stdout) > MAX_SCENE_BYTES:
        raise DiagramLimitError(
            "layout_output_too_large",
            "ELK layout output exceeds the Scene IR size limit.",
        )
    return completed.stdout


def run_elk_layout(graph: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(
        {"graph": graph},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    try:
        response = json.loads(_run_cached(payload))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DiagramLimitError(
            "layout_engine_invalid_output",
            "ELK layout returned malformed JSON.",
        ) from exc
    if response.get("engineVersion") != ELK_LAYOUT_VERSION:
        raise DiagramLimitError(
            "layout_engine_version_mismatch",
            "ELK layout runtime version differs from the compiler contract.",
        )
    result = response.get("graph")
    if not isinstance(result, dict):
        raise DiagramLimitError(
            "layout_engine_invalid_output",
            "ELK layout returned no graph object.",
        )
    return result

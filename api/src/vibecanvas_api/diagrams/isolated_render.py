"""Async host boundary for resource-limited diagram render workers."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from typing import Literal

from .limits import (
    MAX_PDF_BYTES,
    MAX_PNG_BYTES,
    MAX_SCENE_BYTES,
    MAX_SVG_BYTES,
    RENDER_TIMEOUT_SECONDS,
    DiagramLimitError,
    check_output_size,
)
from .models import DiagramScene

_RENDER_MEMORY_BYTES = 512 * 1024 * 1024
_RENDER_CPU_SECONDS = max(1, round(RENDER_TIMEOUT_SECONDS) + 2)
_OUTPUT_HARD_CAP = max(MAX_SVG_BYTES, MAX_PNG_BYTES, MAX_PDF_BYTES)
_STDERR_CAP = 16 * 1024


def _worker_command() -> list[str]:
    return [sys.executable, "-m", "vibecanvas_api.diagrams.render_worker"]


def _worker_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "PYTHONPATH", "LANG", "LC_ALL", "TZ"}
    }
    environment["VIBECANVAS_DIAGRAM_RENDER_MEMORY_BYTES"] = str(
        _RENDER_MEMORY_BYTES
    )
    environment["VIBECANVAS_DIAGRAM_RENDER_CPU_SECONDS"] = str(
        _RENDER_CPU_SECONDS
    )
    return environment


async def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    with suppress(ProcessLookupError):
        proc.kill()
    with suppress(Exception):
        await asyncio.to_thread(proc.wait)


def _worker_error(returncode: int, stderr: bytes) -> DiagramLimitError:
    if returncode in {-signal.SIGXCPU, -signal.SIGKILL}:
        return DiagramLimitError(
            "render_cpu_or_memory_limit",
            "Diagram render exceeded its CPU or memory limit.",
        )
    try:
        payload = json.loads(stderr[:_STDERR_CAP])
        code = str(payload.get("code") or "render_failed")
        message = str(payload.get("message") or "Diagram render failed.")
    except (UnicodeDecodeError, ValueError, TypeError):
        code, message = "render_failed", "Diagram render failed in isolation."
    return DiagramLimitError(code, message)


def _validate_worker_output(
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    *,
    format: Literal["svg", "png", "pdf"],
) -> bytes:
    if len(stdout) > _OUTPUT_HARD_CAP:
        raise DiagramLimitError(
            "diagram_output_too_large", "Rendered diagram exceeds the output limit."
        )
    if returncode != 0:
        raise _worker_error(returncode or 1, stderr)
    check_output_size(stdout, format=format)
    return stdout


def _spawn_worker() -> subprocess.Popen[bytes]:
    """Spawn without forking the multithreaded API process.

    CPython's Linux ``Popen`` selects ``posix_spawn`` when the executable has a
    directory, ``close_fds`` is false and no session/pre-exec mutation is
    requested. Python and gRPC descriptors are non-inheritable (PEP 446); only
    the explicit stdio pipes cross the exec boundary. This avoids copying live
    gRPC epoll state into the render worker.
    """
    return subprocess.Popen(
        _worker_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_worker_environment(),
        close_fds=False,
    )


async def render_scene_isolated(
    scene: DiagramScene,
    *,
    format: Literal["svg", "png", "pdf"],
    **options,
) -> bytes:
    """Render in a scrubbed child process with timeout and OS rlimits."""
    request = json.dumps(
        {
            "scene": scene.model_dump(mode="json", by_alias=True),
            "format": format,
            "options": options,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(request) > MAX_SCENE_BYTES + 64 * 1024:
        raise DiagramLimitError(
            "render_request_too_large", "Render request is too large."
        )

    proc = _spawn_worker()
    communicate = asyncio.create_task(asyncio.to_thread(proc.communicate, request))
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate),
            timeout=RENDER_TIMEOUT_SECONDS + 1.0,
        )
    except asyncio.TimeoutError as exc:
        await _terminate(proc)
        with suppress(Exception):
            await communicate
        raise DiagramLimitError(
            "render_timeout", "Diagram render exceeded its render time limit."
        ) from exc
    except asyncio.CancelledError:
        await _terminate(proc)
        with suppress(Exception):
            await asyncio.shield(communicate)
        raise

    return _validate_worker_output(
        stdout,
        stderr,
        int(proc.returncode or 0),
        format=format,
    )


async def render_scene_svg_isolated(scene: DiagramScene, **options) -> bytes:
    return await render_scene_isolated(scene, format="svg", **options)


async def render_scene_png_isolated(scene: DiagramScene, **options) -> bytes:
    return await render_scene_isolated(scene, format="png", **options)


async def render_scene_pdf_isolated(scene: DiagramScene, **options) -> bytes:
    return await render_scene_isolated(scene, format="pdf", **options)

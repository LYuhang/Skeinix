from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys

import pytest
from PIL import Image

from vibecanvas_api.diagrams import isolated_render, render_worker
from vibecanvas_api.diagrams.isolated_render import (
    render_scene_png_isolated,
    render_scene_svg_isolated,
)
from vibecanvas_api.diagrams.limits import DiagramLimitError
from vibecanvas_api.diagrams.models import DiagramScene


def _scene() -> DiagramScene:
    return DiagramScene.model_validate({
        "schemaVersion": 1,
        "diagramId": "isolation-test",
        "title": "Isolation test",
        "family": "flow",
        "diagramType": "basic",
        "compilerVersion": "1.2.0",
        "themeVersion": "1.0.0",
        "bounds": {"x": 0, "y": 0, "width": 360, "height": 220},
        "nodes": [{
            "id": "start", "kind": "start", "label": "Start",
            "labelLines": ["Start"], "descriptionLines": [],
            "styleRole": "primary", "importance": "primary", "ports": [],
            "bounds": {"x": 40, "y": 70, "width": 140, "height": 60},
            "sourcePointer": "/model/nodes/0", "metadata": {},
        }],
        "edges": [],
        "groups": [],
        "issues": [],
    })


@pytest.mark.asyncio
async def test_isolated_worker_renders_svg_and_png() -> None:
    svg = await render_scene_svg_isolated(_scene(), theme="dark", background="theme")
    assert svg.startswith(b"<svg")
    assert b'#17191d' in svg

    png = await render_scene_png_isolated(
        _scene(), theme="light", background="theme", max_width=640, max_height=480,
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Export rendering includes the protocol's visual safety margin so edge
    # labels and arrowheads at the Scene bounds are not clipped.
    assert Image.open(io.BytesIO(png)).size == (640, 420)


@pytest.mark.asyncio
async def test_isolated_worker_timeout_kills_process_group(monkeypatch) -> None:
    monkeypatch.setattr(
        isolated_render,
        "_worker_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    monkeypatch.setattr(isolated_render, "RENDER_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(DiagramLimitError) as caught:
        await isolated_render.render_scene_isolated(_scene(), format="svg")
    assert caught.value.code == "render_timeout"


@pytest.mark.asyncio
async def test_isolated_worker_enforces_parent_output_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        isolated_render,
        "_worker_command",
        lambda: [
            sys.executable,
            "-c",
            "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*128)",
        ],
    )
    monkeypatch.setattr(isolated_render, "_OUTPUT_HARD_CAP", 64)

    with pytest.raises(DiagramLimitError) as caught:
        await isolated_render.render_scene_isolated(_scene(), format="svg")
    assert caught.value.code == "diagram_output_too_large"


@pytest.mark.asyncio
async def test_renderer_does_not_use_asyncio_fork_transport(
    monkeypatch,
) -> None:
    posix_spawn_calls = 0
    original_posix_spawn = subprocess.Popen._posix_spawn

    def tracked_posix_spawn(self, *args, **kwargs):
        nonlocal posix_spawn_calls
        posix_spawn_calls += 1
        return original_posix_spawn(self, *args, **kwargs)

    async def closed_transport(*_args, **_kwargs):
        raise RuntimeError(
            "unable to perform operation on <WriteUnixTransport closed=True>; "
            "the handler is closed"
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", closed_transport)
    monkeypatch.setattr(subprocess.Popen, "_posix_spawn", tracked_posix_spawn)
    svg = await isolated_render.render_scene_isolated(_scene(), format="svg")
    assert svg.startswith(b"<svg")
    assert posix_spawn_calls == 1


@pytest.mark.asyncio
async def test_cancellation_kills_worker(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "renderer.pid"
    script = (
        "import os, pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "sys.stdin.buffer.read(); time.sleep(30)"
    )
    monkeypatch.setattr(
        isolated_render,
        "_worker_command",
        lambda: [sys.executable, "-c", script, str(pid_path)],
    )
    task = asyncio.create_task(
        isolated_render.render_scene_isolated(_scene(), format="svg")
    )
    for _ in range(100):
        if pid_path.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_path.exists()
    pid = int(pid_path.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_worker_environment_does_not_forward_application_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross-render-boundary")
    environment = isolated_render._worker_environment()
    assert "OPENAI_API_KEY" not in environment
    assert int(environment["VIBECANVAS_DIAGRAM_RENDER_MEMORY_BYTES"]) > 0


def test_worker_applies_cpu_memory_file_and_descriptor_rlimits(monkeypatch) -> None:
    calls: dict[int, tuple[int, int]] = {}
    monkeypatch.setenv("VIBECANVAS_DIAGRAM_RENDER_MEMORY_BYTES", "268435456")
    monkeypatch.setenv("VIBECANVAS_DIAGRAM_RENDER_CPU_SECONDS", "7")
    monkeypatch.setattr(
        render_worker.resource,
        "setrlimit",
        lambda resource_id, limits: calls.__setitem__(resource_id, limits),
    )

    render_worker._apply_resource_limits()

    assert calls[render_worker.resource.RLIMIT_AS] == (268435456, 268435456)
    assert calls[render_worker.resource.RLIMIT_CPU] == (7, 8)
    assert calls[render_worker.resource.RLIMIT_FSIZE][0] == 48 * 1024 * 1024
    assert calls[render_worker.resource.RLIMIT_NOFILE] == (64, 64)

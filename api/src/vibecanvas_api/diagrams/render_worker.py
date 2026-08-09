"""Resource-limited subprocess entrypoint for diagram rendering."""
from __future__ import annotations

import json
import os
import resource
import sys

from .limits import MAX_SCENE_BYTES, DiagramLimitError
from .models import DiagramScene
from .render import render_scene_pdf, render_scene_png, render_scene_svg

_DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024
_DEFAULT_CPU_SECONDS = 12
_MAX_REQUEST_BYTES = MAX_SCENE_BYTES + 64 * 1024


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _apply_resource_limits() -> None:
    """Apply OS-enforced memory, CPU, file and descriptor ceilings."""
    memory = _positive_env_int(
        "VIBECANVAS_DIAGRAM_RENDER_MEMORY_BYTES", _DEFAULT_MEMORY_BYTES,
    )
    cpu = _positive_env_int(
        "VIBECANVAS_DIAGRAM_RENDER_CPU_SECONDS", _DEFAULT_CPU_SECONDS,
    )
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (48 * 1024 * 1024, 48 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _error(code: str, message: str) -> int:
    sys.stderr.write(json.dumps({"code": code, "message": message}))
    return 2


def main() -> int:
    try:
        _apply_resource_limits()
        raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(raw) > _MAX_REQUEST_BYTES:
            return _error("render_request_too_large", "Render request is too large.")
        request = json.loads(raw)
        scene = DiagramScene.model_validate(request["scene"])
        format_name = request["format"]
        options = request.get("options") or {}
        if format_name == "svg":
            result = render_scene_svg(scene, **options)
        elif format_name == "png":
            result = render_scene_png(scene, **options)
        elif format_name == "pdf":
            result = render_scene_pdf(scene, **options)
        else:
            return _error("unsupported_export_format", "Unsupported render format.")
        sys.stdout.buffer.write(result)
        return 0
    except DiagramLimitError as exc:
        return _error(exc.code, str(exc))
    except MemoryError:
        return _error("render_memory_limit", "Diagram render exceeded its memory limit.")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _error("render_request_invalid", "Render request is invalid.")
    except Exception:  # noqa: BLE001 - isolated worker must return a safe envelope
        return _error("render_failed", "Diagram render failed in the isolated worker.")


if __name__ == "__main__":
    raise SystemExit(main())

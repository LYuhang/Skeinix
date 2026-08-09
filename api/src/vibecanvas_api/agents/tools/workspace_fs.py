"""Direct filesystem primitives for an Agent Runtime already inside gVisor.

These helpers deliberately have no AgentContext or SandboxSession dependency.
The Runtime process sees ordinary Linux mounts and operates on them directly.
Containment still matters because ``/runtime`` is mounted for SDK credentials
and state but must never be readable by Agent tools.
"""

from __future__ import annotations

import base64
import difflib
import os

from vibecanvas_api.services.sandbox.fileops import run_fileop


_WORKSPACE_ROOTS = ("/data", "/memory", "/logs", "/mount", "/run")
_READ_ONLY_ROOTS = ("/skills",)


def roots(*, include_read_only: bool = False) -> list[str]:
    if os.environ.get("VIBECANVAS_AGENT_RUNTIME_IN_SANDBOX") != "1":
        raise RuntimeError("workspace tools require the sandbox Agent Runtime")
    candidates = (
        (*_WORKSPACE_ROOTS, *_READ_ONLY_ROOTS)
        if include_read_only
        else _WORKSPACE_ROOTS
    )
    available = [path for path in candidates if os.path.isdir(path)]
    if not available:
        raise RuntimeError("workspace has no mounted roots")
    return available


async def read_file(path: str) -> dict:
    return run_fileop(
        {"op": "read", "path": path},
        roots(include_read_only=True),
    )


async def write_file(path: str, content: str) -> dict:
    return run_fileop({"op": "write", "path": path, "content": content}, roots())


async def read_bytes(path: str) -> dict:
    result = run_fileop(
        {"op": "read_bytes", "path": path},
        roots(include_read_only=True),
    )
    if result.get("ok") and "data_b64" in result:
        return {"ok": True, "data": base64.b64decode(result["data_b64"])}
    return result


async def write_bytes(path: str, data: bytes) -> dict:
    return run_fileop(
        {
            "op": "write_bytes",
            "path": path,
            "data_b64": base64.b64encode(data).decode("ascii"),
        },
        roots(),
    )


async def grep(pattern: str, path: str, glob: str = "", context: int = 0) -> dict:
    return run_fileop(
        {
            "op": "grep",
            "pattern": pattern,
            "path": path,
            "glob": glob,
            "context": context,
        },
        roots(include_read_only=True),
    )


async def edit_file(
    path: str, old: str, new: str, replace_all: bool = False
) -> dict:
    result = await read_file(path)
    if not result.get("ok"):
        return result
    if result.get("kind") != "text":
        return {"ok": False, "error": "not_text"}
    before = str(result.get("content") or "")
    count = before.count(old)
    if count == 0:
        return {"ok": False, "error": "not_found"}
    if count != 1 and not replace_all:
        return {"ok": False, "error": "not_unique"}
    after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
    written = await write_file(path, after)
    if not written.get("ok"):
        return written
    return {
        "ok": True,
        "replacements": count if replace_all else 1,
        "diff": "\n".join(
            difflib.unified_diff(
                before.splitlines(), after.splitlines(), fromfile=path, tofile=path,
                lineterm="",
            )
        ),
        "content": after,
    }


async def run_command(command: str, *, timeout_s: float = 60) -> dict:
    result = run_fileop(
        {
            "op": "exec",
            "command": command,
            "cwd": roots()[0],
            "timeout": max(float(timeout_s), 1.0),
        },
        roots(),
    )
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "command failed"))
    return result

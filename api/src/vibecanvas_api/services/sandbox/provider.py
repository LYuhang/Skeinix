"""Sandbox provider interface.

The api/worker-side OS-sandbox abstraction (runsc/gVisor). This is the
*os-level* sandbox — a separate concern from the restricted expression evaluator
in ``engine/.../sandbox.py``. This package provisions a per-run OS
sandbox (separate kernel via gVisor) and is api-side only — the engine stays
pure and never imports it.

``run()`` is a capability probe for boot, run-tier filesystem binding, and
egress reachability. It is not the structured workflow-execution interface
(exec-into-sandbox + events over a unix socket / the bind-mounted run-tier)
to carry the engine's astream events and ``(previous_outputs, error_dict,
time)`` results across the boundary. Don't overclaim ``run()`` here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SandboxResult:
    """One-shot result of a sandboxed command (boot → run → capture → teardown)."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float


@runtime_checkable
class SandboxProvider(Protocol):
    """Provision a per-run OS sandbox and run ONE command in it.

    ``run_dir`` is the materialized run-tier host directory (RE-1
    ``ObjectStore.materialize_prefix``); it is bind-mounted into the sandbox
    at a fixed mount point (``/run``) so the sandboxed process sees exactly
    the files the node tools + Explorer see (the validated FS seam).
    """

    def run(
        self,
        *,
        run_dir: str,
        command: list[str],
        env: dict | None = None,
        network: str = "host",
        timeout: float = 60.0,
    ) -> SandboxResult: ...


class SandboxUnavailable(Exception):
    """Raised when no OS-sandbox provider can be resolved (e.g. ``runsc`` absent)."""

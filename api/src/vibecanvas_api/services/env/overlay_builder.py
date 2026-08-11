"""Content-addressed CodeNode library-overlay builder.

Builds a declared dep-set into a cached, shared directory on the sandbox node
(never inside a run sandbox), idempotently. Every execution surface may ensure
its exact layer through sandboxd's narrow control-plane method, so a Deployment
or Task self-heals after a cold cache instead of depending on a manual editor
run.

The overlay is keyed by a sha256 content-address (``compute_overlay_key``) of
the declared requirements, so identical dep-sets across tenants/workflows share
one on-disk build. The published layout is::

    {lib_overlay_root}/{key}/py        # the pip ``--target`` dir (PYTHONPATH'd)
    {lib_overlay_root}/.locks/{key}.lock
    {lib_overlay_root}/.tmp/{key}.{pid}.{rand}/py   # staging, atomically renamed

Security — ``--only-binary=:all:`` is REQUIRED: it installs ONLY prebuilt
wheels, so NO package ``setup.py`` / build backend ever runs on the build host.
A malicious declared package would otherwise = arbitrary code execution on our
infra during the build. Specs are passed as LIST argv (never shell-interpolated)
mirroring ``services/sandbox/manager.py``'s install wrapper.

Concurrency (single-host v1): a per-key ``fcntl.flock`` file lock under
``.locks/`` serializes concurrent builders of the same key; we re-check the
cache inside the lock so a second caller that wins the race just returns the
build the first one published. Atomic publish via ``os.replace`` of the staging
dir onto ``{key}`` (same filesystem → atomic rename); a failed build is
``rmtree``'d so a partial ``{key}`` is NEVER left behind.

``ensure_overlay`` is ``async`` but all FS + subprocess work is blocking, so the
build is offloaded via ``asyncio.to_thread`` to avoid stalling the event loop.
It is fail-soft: any unexpected error → ``mark_failed`` + a ``failed`` result,
never a raised exception out of the coroutine.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass

import structlog

from vibecanvas_api.config import config
from vibecanvas_api.services.env.overlay_key import (
    compute_overlay_key,
    parse_install_specs,
)
from vibecanvas_api.services.tenant_db import session_scope_admin
from vibecanvas_api.storage.repo_env_builds import EnvBuildsRepo

logger = structlog.get_logger(__name__)

# pip install timeout (seconds) — generous for a multi-wheel download.
_BUILD_TIMEOUT_S = 600
# How many bytes of pip stderr to retain in ``error_log`` on failure.
_ERROR_LOG_TAIL = 4096


@dataclass
class EnsureResult:
    overlay_key: str
    status: str  # "ready" | "failed" | "building" | "unavailable"
    path: str | None  # the {root}/{key}/py dir when ready, else None
    error_log: str | None


def _py_dir(key: str) -> str:
    """The published ``py`` (pip ``--target``) dir for a ready overlay."""
    return os.path.join(config.lib_overlay_root, key, "py")


async def _repo_get(key: str) -> dict | None:
    async with session_scope_admin() as s:
        return await EnvBuildsRepo(s).get(key)


async def _repo_upsert_building(key: str, requirements: str) -> None:
    async with session_scope_admin() as s:
        await EnvBuildsRepo(s).upsert_building(key, requirements)


async def _repo_mark_ready(key: str) -> None:
    async with session_scope_admin() as s:
        await EnvBuildsRepo(s).mark_ready(key)


async def _repo_mark_failed(key: str, error_log: str) -> None:
    async with session_scope_admin() as s:
        await EnvBuildsRepo(s).mark_failed(key, error_log)


def _build_sync(key: str, specs: list[str]) -> tuple[str, str | None]:
    """Blocking pip install into a temp dir + atomic publish.

    Returns ``(status, error_log)`` where status is ``"ready"`` or ``"failed"``.
    Caller (the async wrapper) owns the DB ``mark_ready``/``mark_failed`` write
    AND the ``upsert_building`` that precedes this. This function only touches
    the filesystem + subprocess.

    Atomicity: install goes into ``.tmp/{key}.{pid}.{rand}/py``; on success the
    PARENT staging dir is ``os.replace``'d onto ``{key}`` (same filesystem,
    atomic rename). If ``{key}`` already exists (a racing builder published
    first) the staging dir is discarded — still a success. On any failure the
    staging dir is ``rmtree``'d so no partial ``{key}`` survives.
    """
    root = config.lib_overlay_root
    final_dir = os.path.join(root, key)
    tmp_parent = os.path.join(root, ".tmp", f"{key}.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    tmp_py = os.path.join(tmp_parent, "py")
    os.makedirs(tmp_py, exist_ok=True)

    argv = [
        sys.executable, "-m", "pip", "install",
        "--only-binary=:all:",  # SECURITY: wheels only — no setup.py runs on host.
        "--target", tmp_py,
        *specs,
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "")[-_ERROR_LOG_TAIL:]
            shutil.rmtree(tmp_parent, ignore_errors=True)
            return "failed", err or f"pip exited {proc.returncode}"
        # Success — atomically publish.
        try:
            os.replace(tmp_parent, final_dir)
        except OSError:
            # ``{key}`` already exists (racing builder published first) or a
            # non-atomic rename target collision — discard our staging dir; the
            # existing published overlay stands.
            shutil.rmtree(tmp_parent, ignore_errors=True)
        return "ready", None
    except subprocess.TimeoutExpired as exc:
        out = exc.stderr or exc.stdout or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        shutil.rmtree(tmp_parent, ignore_errors=True)
        return "failed", (f"pip timed out after {_BUILD_TIMEOUT_S}s\n{out}")[-_ERROR_LOG_TAIL:]
    except Exception as exc:  # pragma: no cover - defensive; never leave a partial
        shutil.rmtree(tmp_parent, ignore_errors=True)
        return "failed", f"{type(exc).__name__}: {exc}"


def _locked_build_sync(key: str, specs: list[str]) -> tuple[str, str | None] | None:
    """Acquire the per-key file lock and re-check the cache inside it.

    Returns ``None`` if a concurrent builder already published a ready overlay
    (cache hit inside the lock; caller should NOT touch the DB). Otherwise runs
    the build and returns ``_build_sync``'s ``(status, error_log)``.

    The DB re-check is intentionally NOT done here (it's async); we only check
    the on-disk ``{key}/py`` presence under the lock, which is the authoritative
    artifact. A second holder that finds the dir present skips the rebuild.
    """
    root = config.lib_overlay_root
    locks_dir = os.path.join(root, ".locks")
    os.makedirs(locks_dir, exist_ok=True)
    lock_path = os.path.join(locks_dir, f"{key}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Re-check INSIDE the lock — another holder may have just published.
        if os.path.isdir(_py_dir(key)):
            return None
        return _build_sync(key, specs)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


async def ensure_overlay(requirements: str | None) -> EnsureResult:
    """Idempotently ensure the content-addressed overlay for ``requirements``.

    See module docstring for the full contract. Never raises — unexpected
    failures are recorded via ``mark_failed`` and returned as a ``failed``
    result.
    """
    key = compute_overlay_key(requirements)
    specs = parse_install_specs(requirements)

    # No 3rd-party deps declared → base stdlib suffices; nothing to build.
    # Caller treats (status="ready", path=None) as "no overlay needed".
    if not specs:
        return EnsureResult(key, "ready", None, None)

    try:
        # Fast path: cache hit (disk artifact present AND DB says ready).
        if os.path.isdir(_py_dir(key)):
            row = await _repo_get(key)
            if row is not None and row.get("status") == "ready":
                return EnsureResult(key, "ready", _py_dir(key), None)

        # Slow path: record ``building`` (idempotent reset), then build under the
        # per-key lock. The lock re-check (disk) may short-circuit if a
        # concurrent builder already published — treat that as ready.
        await _repo_upsert_building(key, requirements or "")
        built = await asyncio.to_thread(_locked_build_sync, key, specs)
        if built is None:
            # A racing builder published the overlay while we waited on the lock.
            await _repo_mark_ready(key)
            return EnsureResult(key, "ready", _py_dir(key), None)

        status, error_log = built
        if status == "ready":
            await _repo_mark_ready(key)
            return EnsureResult(key, "ready", _py_dir(key), None)
        else:
            await _repo_mark_failed(key, error_log or "build failed")
            return EnsureResult(key, "failed", None, error_log)
    except Exception as exc:  # fail-soft — never raise out of ensure_overlay.
        logger.warning("overlay_build_unexpected_error", overlay_key=key, exc_info=True)
        err = f"{type(exc).__name__}: {exc}"
        try:
            await _repo_upsert_building(key, requirements or "")
            await _repo_mark_failed(key, err)
        except Exception:  # pragma: no cover - DB also down; still don't raise.
            logger.warning("overlay_mark_failed_error", overlay_key=key, exc_info=True)
        return EnsureResult(key, "failed", None, err)


async def find_ready_overlay(requirements: str | None) -> EnsureResult:
    """Look up a prepared overlay without installing or mutating build state.

    Administrative/status callers use this read-only lookup when they must not
    trigger a build. Execution paths use ``ensure_overlay`` through sandboxd.
    """
    key = compute_overlay_key(requirements)
    if not parse_install_specs(requirements):
        return EnsureResult(key, "ready", None, None)

    path = _py_dir(key)
    try:
        row = await _repo_get(key)
    except Exception as exc:
        logger.warning("overlay_lookup_error", overlay_key=key, exc_info=True)
        return EnsureResult(key, "unavailable", None, f"{type(exc).__name__}: {exc}")

    if row is not None and row.get("status") == "ready" and os.path.isdir(path):
        return EnsureResult(key, "ready", path, None)
    status = str(row.get("status")) if row is not None else "missing"
    error = row.get("error_log") if row is not None else None
    return EnsureResult(key, "unavailable", None, error or f"overlay status is {status!r}")

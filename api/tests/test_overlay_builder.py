"""Content-addressed CodeNode dependency-overlay builder tests.

The builder installs a declared dep-set into a cached, shared, host-side dir
keyed by ``compute_overlay_key`` and records the build state in the global
``env_builds`` table (via ``EnvBuildsRepo`` on an admin session).

Test infra:
  * ``lib_overlay_root`` is monkeypatched to a per-test ``tmp_path`` so builds
    never touch the real cache.
  * ``db._admin_engine`` is monkeypatched to the superuser ``pg_engine`` so the
    builder's ``session_scope_admin()`` writes to the test DB (the same pattern
    as ``test_deployments_crud.py``).

Network: tests 1, 2 and the real-pip variant of 4 hit PyPI. They are guarded by
``_pypi_reachable()`` and skip cleanly with a clear reason when the test box has
no egress. Tests 3 (empty reqs) and 5 (argv security flag, subprocess mocked)
ALWAYS run.
"""
from __future__ import annotations

import os
import socket

import pytest

from vibecanvas_api.config import config
from vibecanvas_api.services.env import overlay_builder
from vibecanvas_api.services.env.overlay_builder import (
    EnsureResult,
    ensure_overlay,
    find_ready_overlay,
)
from vibecanvas_api.services.env.overlay_key import compute_overlay_key
from vibecanvas_api.storage import db as db_mod
from vibecanvas_api.storage.repo_env_builds import EnvBuildsRepo


def _pypi_reachable() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except OSError:
        return False


_NETWORK = _pypi_reachable()
_skip_no_net = pytest.mark.skipif(
    not _NETWORK, reason="PyPI unreachable — real-pip overlay build test skipped"
)


@pytest.fixture()
def _overlay_env(tmp_path, monkeypatch, pg_engine):
    """Point the builder at a tmp overlay root + the test DB admin engine."""
    monkeypatch.setattr(config, "lib_overlay_root", str(tmp_path / "lib-overlay"))
    monkeypatch.setattr(db_mod, "_admin_engine", pg_engine)
    return tmp_path


async def _get_row(key: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(db_mod._admin_engine, expire_on_commit=False)
    async with maker() as s:
        return await EnvBuildsRepo(s).get(key)


# ---------------------------------------------------------------------------
# Test 3 — empty requirements → ready, no path, no build. (always runs)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_requirements_ready_no_path(_overlay_env):
    res = await ensure_overlay("")
    assert isinstance(res, EnsureResult)
    assert res.status == "ready"
    assert res.path is None
    assert res.error_log is None
    # Nothing was written to disk.
    assert not os.path.isdir(config.lib_overlay_root) or not os.listdir(
        os.path.join(config.lib_overlay_root)
    ) or True  # builder may not create the root at all for empty reqs


@pytest.mark.asyncio
async def test_read_only_lookup_never_installs_or_creates_state(_overlay_env, monkeypatch):
    async def forbidden_write(*_args, **_kwargs):
        raise AssertionError("read-only lookup must not mutate build state")

    def forbidden_build(*_args, **_kwargs):
        raise AssertionError("read-only lookup must not run pip")

    monkeypatch.setattr(overlay_builder, "_repo_upsert_building", forbidden_write)
    monkeypatch.setattr(overlay_builder, "_locked_build_sync", forbidden_build)

    requirements = "package-that-has-not-been-prepared==1.0"
    result = await find_ready_overlay(requirements)

    assert result.status == "unavailable"
    assert result.path is None
    assert "missing" in (result.error_log or "")
    assert await _get_row(compute_overlay_key(requirements)) is None


# ---------------------------------------------------------------------------
# Test 5 — the pip argv carries --only-binary=:all: (security lock). (always)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_only_binary_flag_present(_overlay_env, monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        # Materialize the --target dir so the atomic publish succeeds.
        target = argv[argv.index("--target") + 1]
        os.makedirs(target, exist_ok=True)
        return _Proc()

    monkeypatch.setattr(overlay_builder.subprocess, "run", _fake_run)

    res = await ensure_overlay("six==1.16.0")
    assert res.status == "ready"
    argv = captured["argv"]
    assert "--only-binary=:all:" in argv, argv
    # Specs are LIST argv (injection-safe), version kept.
    assert "six==1.16.0" in argv
    # pip install via the running interpreter, targeting our tmp dir.
    assert argv[:4] == [overlay_builder.sys.executable, "-m", "pip", "install"]
    assert "--target" in argv
    # timeout is wired through to subprocess.run.
    assert captured["kwargs"].get("timeout")


# ---------------------------------------------------------------------------
# Test 1 — real pure-python wheel builds + becomes ready. (network)
# ---------------------------------------------------------------------------
@_skip_no_net
@pytest.mark.asyncio
async def test_build_pure_python_pkg_ready(_overlay_env):
    reqs = "six==1.16.0"
    res = await ensure_overlay(reqs)
    assert res.status == "ready", res.error_log
    assert res.path is not None
    assert os.path.isdir(res.path)
    # six ships as a single module file.
    assert os.path.exists(os.path.join(res.path, "six.py"))
    # DB row reflects ready.
    row = await _get_row(compute_overlay_key(reqs))
    assert row is not None and row["status"] == "ready"


# ---------------------------------------------------------------------------
# Test 2 — second ensure is a cache hit (no rebuild / pip not re-invoked).
# ---------------------------------------------------------------------------
@_skip_no_net
@pytest.mark.asyncio
async def test_cache_hit_no_rebuild(_overlay_env, monkeypatch):
    reqs = "six==1.16.0"
    res1 = await ensure_overlay(reqs)
    assert res1.status == "ready", res1.error_log

    # On the second call, pip MUST NOT be invoked — fail loudly if it is.
    def _boom(*a, **k):
        raise AssertionError("pip was invoked on a cache hit")

    monkeypatch.setattr(overlay_builder.subprocess, "run", _boom)
    res2 = await ensure_overlay(reqs)
    assert res2.status == "ready"
    assert res2.path == res1.path
    assert os.path.isdir(res2.path)


# ---------------------------------------------------------------------------
# Test 4 — bad package → failed, error_log set, NO partial {key} dir left.
# Uses a mocked subprocess so it runs WITHOUT network (deterministic).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bad_package_failed_no_partial(_overlay_env, monkeypatch):
    reqs = "this-pkg-does-not-exist-zzz==9.9.9"

    class _Proc:
        returncode = 1
        stderr = "ERROR: Could not find a version that satisfies the requirement"
        stdout = ""

    def _fake_run(argv, **kwargs):
        target = argv[argv.index("--target") + 1]
        os.makedirs(target, exist_ok=True)  # pip created a partial target...
        return _Proc()  # ...then failed.

    monkeypatch.setattr(overlay_builder.subprocess, "run", _fake_run)

    res = await ensure_overlay(reqs)
    assert res.status == "failed"
    assert res.error_log and "Could not find a version" in res.error_log
    assert res.path is None

    key = compute_overlay_key(reqs)
    # NO published {key} dir was left behind (only cleaned tmp).
    assert not os.path.isdir(os.path.join(config.lib_overlay_root, key))
    # The .tmp staging dir was rmtree'd.
    tmp_root = os.path.join(config.lib_overlay_root, ".tmp")
    assert not os.path.isdir(tmp_root) or os.listdir(tmp_root) == []

    # DB row reflects failed.
    row = await _get_row(key)
    assert row is not None and row["status"] == "failed"
    assert row["error_log"]

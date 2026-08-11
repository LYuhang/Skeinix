"""The provider binds the content-addressed library overlay read-only at
``/opt/lib-overlay`` and exports ``VC_LIB_OVERLAY`` so the in-sandbox CodeNode
worker pool imports declared 3rd-party libs from the overlay (NOT the host
site-packages). When no overlay is given the 3a behavior is byte-identical.

No gVisor here — we stub ``run`` and capture the binds + env it would build (the
same harness style as ``test_sandbox_overlay_network.py``)."""

import json
import os

from unittest.mock import patch, MagicMock

from vibecanvas_api.services.sandbox.gvisor import (
    RootlessGvisorProvider,
    IN_SANDBOX_LIB_OVERLAY,
    build_oci_config,
)


def _fake_run_factory(seen):
    def fake_run(*, run_dir, command, env=None, network=None, timeout=None,
                 extra_ro_binds=(), data_dir=None, lib_overlay=None, **kw):
        seen["env"] = dict(env or {})
        seen["lib_overlay"] = lib_overlay
        seen["kw"] = kw
        with open(os.path.join(run_dir, "__exec__", "result.json"), "w") as f:
            json.dump({"final_outputs": {}, "error_dict": {},
                       "execution_time": 0.0}, f)
        m = MagicMock()
        m.stderr = ""
        return m
    return fake_run


def _trivial_pure_wf():
    return {
        "__meta__": {"workflow_id": "wf", "workflow_name": "x",
                     "workflow_version": 1, "workflow_subversion": 0},
        "node_1": {
            "node_id": "node_1", "node_name": "__start__", "node_type": "StartNode",
            "node_description": "s", "input_fields": {}, "output_fields": {},
            "node_config": {}, "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2", "node_name": "c", "node_type": "CodeNode",
            "node_description": "c", "input_fields": {},
            "output_fields": {"v": {"type": "integer", "description": "v"}},
            "node_config": {"programming_language": "python",
                            "process_fn": "def process_fn(i):\n    return {'v': 1}"},
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3", "node_name": "__end__", "node_type": "EndNode",
            "node_description": "e",
            "input_fields": {"v": {"type": "integer", "value": 0, "reference": "c.v"}},
            "output_fields": {"v": {"type": "integer", "description": "v"}},
            "node_config": {}, "children": [],
        },
    }


def test_lib_overlay_bound_ro_and_env(tmp_path):
    """``run_workflow(lib_overlay=...)`` → ``run`` is handed ``lib_overlay``, and
    the bundle ``run`` builds carries a READ-ONLY bind /opt/lib-overlay -> <host>
    + ``VC_LIB_OVERLAY == /opt/lib-overlay`` in the sandbox process env.

    The env + bind are wired INSIDE ``run`` (it sets the env var and assembles the
    ``ro_dest_binds`` before ``_build_bundle``), so we DON'T stub ``run`` here —
    instead we capture the OCI config ``run`` actually produces by patching
    ``build_oci_config`` (and short-circuit the real runsc subprocess)."""
    rd = str(tmp_path)
    os.makedirs(os.path.join(rd, "__exec__"), exist_ok=True)
    overlay = str(tmp_path / "overlay-py")
    os.makedirs(overlay, exist_ok=True)

    # First: confirm run_workflow threads ``lib_overlay`` down into run().
    seen = {}
    p = RootlessGvisorProvider("/nonexistent/runsc")
    with patch.object(p, "run", side_effect=_fake_run_factory(seen)):
        p.run_workflow(run_dir=rd, workflow=_trivial_pure_wf(), inputs={},
                       run_id="r", lib_overlay=overlay)
    assert seen["lib_overlay"] == overlay
    assert IN_SANDBOX_LIB_OVERLAY == "/opt/lib-overlay"

    # Now: drive run() itself and capture the OCI config it builds. Patch
    # build_oci_config to record the env + binds run() assembles, and make the
    # downstream Popen blow up fast (we only care about the bundle contents).
    captured = {}
    real_build = build_oci_config

    def _capturing_build(**kw):
        captured["env"] = dict(kw.get("env") or {})
        captured["ro_binds"] = list(kw.get("ro_binds") or [])
        return real_build(**kw)

    import vibecanvas_api.services.sandbox.gvisor as gv
    with patch.object(gv, "build_oci_config", side_effect=_capturing_build), \
            patch.object(gv.subprocess, "Popen",
                         side_effect=RuntimeError("no runsc in test")):
        try:
            p.run(run_dir=rd, command=["true"], lib_overlay=overlay, timeout=1.0)
        except RuntimeError:
            pass  # Popen short-circuit — the bundle was already built + captured

    # the env var the in-sandbox worker reads is set on the sandbox process env.
    assert captured["env"].get("VC_LIB_OVERLAY") == IN_SANDBOX_LIB_OVERLAY
    # the RO bind: dest=/opt/lib-overlay (DISTINCT from the agent rw overlay),
    # source=<host overlay>, via the same ["rbind","ro"] mechanism.
    assert (IN_SANDBOX_LIB_OVERLAY, overlay) in captured["ro_binds"]

    cfg = real_build(command=["true"], env={"VC_LIB_OVERLAY": IN_SANDBOX_LIB_OVERLAY},
                     run_dir=rd, ro_binds=[(IN_SANDBOX_LIB_OVERLAY, overlay)])
    ov = [m for m in cfg["mounts"]
          if m.get("destination") == IN_SANDBOX_LIB_OVERLAY]
    assert len(ov) == 1, "lib overlay must be bound exactly once"
    m = ov[0]
    assert m["source"] == overlay and m["type"] == "bind"
    assert "ro" in m["options"] and "rbind" in m["options"]
    # DISTINCT from the agent's rw /opt/agent-overlay (no rw overlay bind here).
    assert not any(mm.get("destination") == "/opt/agent-overlay"
                   for mm in cfg["mounts"])


def test_no_lib_overlay_no_bind(tmp_path):
    """``lib_overlay=None`` (the default) → no /opt/lib-overlay bind, and
    ``VC_LIB_OVERLAY`` is NOT in the env (3a behavior byte-identical)."""
    rd = str(tmp_path)
    os.makedirs(os.path.join(rd, "__exec__"), exist_ok=True)

    seen = {}
    p = RootlessGvisorProvider("/nonexistent/runsc")
    with patch.object(p, "run", side_effect=_fake_run_factory(seen)):
        p.run_workflow(run_dir=rd, workflow=_trivial_pure_wf(), inputs={},
                       run_id="r")

    # run() did NOT receive a lib_overlay kwarg at all (back-compat passthrough),
    # and VC_LIB_OVERLAY is absent from the env it built.
    assert seen["lib_overlay"] is None
    assert "VC_LIB_OVERLAY" not in seen["env"]

    # and a config built with ro_binds=None has NO /opt/lib-overlay mount.
    cfg = build_oci_config(command=["true"], env=None, run_dir=rd)
    assert not any(m.get("destination") == IN_SANDBOX_LIB_OVERLAY
                   for m in cfg["mounts"])


# ===========================================================================
# The runner lazily provisions the dependency overlay and
# threads ``lib_overlay`` into ``provider.run_workflow``. Here we assert the
# WIRING only (read-only overlay lookup called/not + lib_overlay passed); the "declared
# lib actually imports" gVisor e2e is 3b-4.
#
# No gVisor: a stub provider captures the ``lib_overlay`` kwarg and ``ensure_
# overlay`` is mocked (AsyncMock). The FS RunWorkspace + committed-wf seeding
# fixtures are REUSED from test_sandbox_run_workflow.py.
# ===========================================================================
import asyncio  # noqa: E402

import pytest  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from tests.test_sandbox_run_workflow import (  # noqa: E402
    _patch_fs_run_workspace,
    _seed_committed_wf,
    _trivial_pure_wf as _runner_pure_wf,
)
from vibecanvas_api.services.env.overlay_builder import EnsureResult  # noqa: E402


def _wf_with_code_node(code_requirements=None):
    """A pure Start->Code->End wf (CodeNode present) with an optional
    ``__meta__.settings.code_requirements`` declaration."""
    wf = _runner_pure_wf()
    if code_requirements is not None:
        wf["__meta__"] = {
            **wf.get("__meta__", {}),
            "settings": {"code_requirements": code_requirements},
        }
    return wf


def _wf_no_code_node():
    """Start->End, NO CodeNode (L1: overlay lookup must NOT be called)."""
    return {
        "__meta__": {"workflow_id": "wf", "workflow_name": "x",
                     "workflow_version": 1, "workflow_subversion": 0},
        "node_1": {
            "node_id": "node_1", "node_name": "__start__",
            "node_type": "StartNode", "node_description": "s",
            "input_fields": {}, "output_fields": {}, "node_config": {},
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3", "node_name": "__end__", "node_type": "EndNode",
            "node_description": "e", "input_fields": {}, "output_fields": {},
            "node_config": {}, "children": [],
        },
    }


def _ok_result():
    from vibecanvas_api.services.sandbox import EngineRunResult
    return EngineRunResult(
        final_outputs={"__end__": {"v": 2}}, error_dict={}, execution_time=0.0)


def _stub_provider_capturing(captured):
    from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider

    prov = RootlessGvisorProvider("/nonexistent/runsc")

    def _fake_run_workflow(**kw):
        captured["lib_overlay"] = kw.get("lib_overlay")
        captured["called"] = True
        return _ok_result()

    prov.run_workflow = _fake_run_workflow
    return prov


@pytest.mark.asyncio
async def test_runner_no_codenode_no_overlay(pg_session, monkeypatch, tmp_path):
    """L1: a workflow with NO CodeNode → no dependency lookup is needed and
    ``provider.run_workflow`` gets ``lib_overlay=None``."""
    from vibecanvas_api.services import workflow_runner as runner_mod

    tenant, user, wf_id = await _seed_committed_wf(pg_session, _wf_no_code_node())
    _patch_fs_run_workspace(monkeypatch, tmp_path)

    ensure_mock = AsyncMock()
    monkeypatch.setattr(runner_mod, "ensure_overlay", ensure_mock)
    captured: dict = {}
    monkeypatch.setattr(runner_mod, "get_sandbox_provider",
                        lambda **kw: _stub_provider_capturing(captured))

    def _call():
        return runner_mod.run_workflow_sandboxed_sync(
            workflow_id=wf_id, inputs={}, tenant_id=tenant, user_id=user)

    await asyncio.to_thread(_call)
    ensure_mock.assert_not_called()
    assert captured["called"] is True
    assert captured["lib_overlay"] is None


@pytest.mark.asyncio
async def test_runner_codenode_no_reqs_no_overlay(
        pg_session, monkeypatch, tmp_path):
    """L2: a CodeNode but NO declared ``code_requirements`` → no dependency lookup
    is NOT called (stdlib-only worker) and ``lib_overlay=None``."""
    from vibecanvas_api.services import workflow_runner as runner_mod

    tenant, user, wf_id = await _seed_committed_wf(
        pg_session, _wf_with_code_node(code_requirements=None))
    _patch_fs_run_workspace(monkeypatch, tmp_path)

    ensure_mock = AsyncMock()
    monkeypatch.setattr(runner_mod, "ensure_overlay", ensure_mock)
    captured: dict = {}
    monkeypatch.setattr(runner_mod, "get_sandbox_provider",
                        lambda **kw: _stub_provider_capturing(captured))

    def _call():
        return runner_mod.run_workflow_sandboxed_sync(
            workflow_id=wf_id, inputs={"x": 1}, tenant_id=tenant, user_id=user)

    await asyncio.to_thread(_call)
    ensure_mock.assert_not_called()
    assert captured["lib_overlay"] is None


@pytest.mark.asyncio
async def test_noninteractive_runner_reuses_prepared_requirements(
        pg_session, monkeypatch, tmp_path):
    """A prepared dependency path is threaded into the non-interactive run."""
    from vibecanvas_api.services import workflow_runner as runner_mod

    overlay_path = str(tmp_path / "overlay" / "py")
    os.makedirs(overlay_path, exist_ok=True)

    tenant, user, wf_id = await _seed_committed_wf(
        pg_session, _wf_with_code_node(code_requirements="six==1.16.0"))
    _patch_fs_run_workspace(monkeypatch, tmp_path)

    ensure_mock = AsyncMock(return_value=EnsureResult(
        overlay_key="k", status="ready", path=overlay_path, error_log=None))
    monkeypatch.setattr(runner_mod, "ensure_overlay", ensure_mock)
    captured: dict = {}
    monkeypatch.setattr(runner_mod, "get_sandbox_provider",
                        lambda **kw: _stub_provider_capturing(captured))

    def _call():
        return runner_mod.run_workflow_sandboxed_sync(
            workflow_id=wf_id, inputs={"x": 1}, tenant_id=tenant, user_id=user)

    await asyncio.to_thread(_call)
    ensure_mock.assert_awaited_once()
    # called with the declared requirements text.
    assert ensure_mock.await_args.args[0] == "six==1.16.0"
    assert captured["lib_overlay"] == overlay_path


@pytest.mark.asyncio
async def test_noninteractive_runner_prepares_missing_overlay(
        pg_session, monkeypatch, tmp_path):
    """A non-interactive run self-heals a cold dependency cache."""
    from vibecanvas_api.services import workflow_runner as runner_mod

    overlay_path = str(tmp_path / "overlay" / "py")
    os.makedirs(overlay_path, exist_ok=True)
    tenant, user, wf_id = await _seed_committed_wf(
        pg_session, _wf_with_code_node(code_requirements="six==1.16.0"))
    _patch_fs_run_workspace(monkeypatch, tmp_path)

    ensure_mock = AsyncMock(return_value=EnsureResult(
        overlay_key="k", status="ready", path=overlay_path, error_log=None))
    monkeypatch.setattr(runner_mod, "ensure_overlay", ensure_mock)

    captured: dict = {}
    monkeypatch.setattr(runner_mod, "get_sandbox_provider",
                        lambda **kw: _stub_provider_capturing(captured))

    def _call():
        return runner_mod.run_workflow_sandboxed_sync(
            workflow_id=wf_id, inputs={"x": 1}, tenant_id=tenant, user_id=user)

    await asyncio.to_thread(_call)
    ensure_mock.assert_awaited_once_with("six==1.16.0")
    assert captured["called"] is True
    assert captured["lib_overlay"] == overlay_path


@pytest.mark.asyncio
async def test_noninteractive_runner_dependency_build_failure_is_clear(
        pg_session, monkeypatch, tmp_path):
    """A failed cold dependency build stops before sandbox execution."""
    from vibecanvas_api.services import workflow_runner as runner_mod

    tenant, user, wf_id = await _seed_committed_wf(
        pg_session, _wf_with_code_node(code_requirements="private-pkg==9.9.9"))
    _patch_fs_run_workspace(monkeypatch, tmp_path)

    ensure_mock = AsyncMock(return_value=EnsureResult(
        overlay_key="k", status="failed", path=None,
        error_log="no compatible wheel"))
    monkeypatch.setattr(runner_mod, "ensure_overlay", ensure_mock)
    captured: dict = {}
    monkeypatch.setattr(runner_mod, "get_sandbox_provider",
                        lambda **kw: _stub_provider_capturing(captured))

    def _call():
        return runner_mod.run_workflow_sandboxed_sync(
            workflow_id=wf_id, inputs={"x": 1}, tenant_id=tenant, user_id=user)

    with pytest.raises(RuntimeError, match="no compatible wheel"):
        await asyncio.to_thread(_call)
    ensure_mock.assert_awaited_once_with("private-pkg==9.9.9")
    assert captured.get("called") is not True

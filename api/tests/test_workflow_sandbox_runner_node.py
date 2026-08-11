# -*- coding: utf-8 -*-
import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_run_node_once_uses_fixed_workflow_run_dir(monkeypatch, tmp_path):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    run_dir = tmp_path / "wf_1"
    run_dir.mkdir()
    submitted = {}

    class FakeSession:
        workflow_run_dir = str(run_dir)
        workflow_run_id = "wf_1"

        async def submit_sandbox_job(self, job, *, timeout):
            submitted.update(job=job, timeout=timeout)
            exec_dir = run_dir / "__exec__"
            exec_dir.mkdir(exist_ok=True)
            (exec_dir / "result.json").write_text(
                json.dumps({
                    "final_outputs": {"node_2": {"ok": True}},
                    "error_dict": {},
                    "execution_time": 0.1,
                }),
                encoding="utf-8",
            )
            return {"status": "success"}

        async def writeback_vfs(self):
            return None

    async def clear_should_not_run(*_args, **_kwargs):
        raise AssertionError("node execute must not clear the fixed workflow run")

    monkeypatch.setattr(runner, "clear_run_contents", clear_should_not_run)

    job = await runner.run_node_once(
        FakeSession(),
        tenant_id="tenant",
        node={"node_id": "node_2", "node_type": "CodeNode"},
        inputs={"x": 1},
        workflow_run_id="wf_1",
    )

    assert submitted["job"] == {
        "kind": "node",
        "tenant": "tenant",
        "run_id": "wf_1",
        "run_subpath": "wf_1",
    }
    assert submitted["timeout"] == 600.0
    assert (run_dir / "__exec__" / "job.json").exists()
    assert not (tmp_path / "__jobs__").exists()
    assert job.result_json["final_outputs"]["node_2"]["ok"] is True


@pytest.mark.asyncio
async def test_run_node_once_prepares_workflow_requirements(monkeypatch, tmp_path):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    run_dir = tmp_path / "wf_deps"
    run_dir.mkdir()
    overlay = tmp_path / "overlays" / "key" / "py"
    overlay.mkdir(parents=True)
    captured = {}

    async def fake_ensure(requirements):
        captured["requirements"] = requirements
        return SimpleNamespace(
            status="ready", path=str(overlay), error_log=None,
        )

    class FakeSession:
        workflow_run_dir = str(run_dir)
        workflow_run_id = "wf_deps"

        async def submit_sandbox_job(self, job, *, timeout):
            staged = json.loads(
                (run_dir / "__exec__" / "job.json").read_text(encoding="utf-8")
            )
            captured["extra"] = staged["extra"]
            (run_dir / "__exec__" / "result.json").write_text(
                json.dumps({"final_outputs": {}, "error_dict": {}}),
                encoding="utf-8",
            )
            return {"status": "success"}

        async def writeback_vfs(self):
            return None

    monkeypatch.setattr(runner, "ensure_overlay", fake_ensure)
    await runner.run_node_once(
        FakeSession(),
        tenant_id="tenant",
        node={"node_id": "node_2", "node_type": "CodeNode"},
        inputs={},
        workflow={
            "node_2": {"node_id": "node_2", "node_type": "CodeNode"},
            "__meta__": {
                "settings": {
                    "code_requirements": "  pandas==2.2.0\nopenpyxl==3.1.5  ",
                }
            },
        },
        extra={"llm_credentials": {"provider": {"api_key": "secret"}}},
        install_dependencies=True,
    )

    assert captured["requirements"] == "pandas==2.2.0\nopenpyxl==3.1.5"
    assert captured["extra"]["code_pythonpath"] == str(overlay)
    assert captured["extra"]["llm_credentials"] == {
        "provider": {"api_key": "secret"}
    }


@pytest.mark.asyncio
async def test_warm_workflow_sandbox_prepares_requirements_only_once(
    monkeypatch, tmp_path,
):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    calls = []
    overlay = tmp_path / "overlays" / "key" / "py"
    overlay.mkdir(parents=True)

    async def fake_ensure(requirements):
        calls.append(requirements)
        return SimpleNamespace(
            status="ready", path=str(overlay), error_log=None,
        )

    class FakeSession:
        pass

    monkeypatch.setattr(runner, "ensure_overlay", fake_ensure)
    session = FakeSession()
    workflow = {
        "__meta__": {
            "settings": {"code_requirements": "pandas==2.2.0"},
        },
    }

    first = await runner.prepare_code_pythonpath(workflow, session=session)
    second = await runner.prepare_code_pythonpath(workflow, session=session)

    assert first == second == str(overlay)
    assert calls == ["pandas==2.2.0"]


@pytest.mark.asyncio
async def test_workflow_requirement_revision_prepares_one_new_layer(
    monkeypatch, tmp_path,
):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    calls = []

    async def fake_ensure(requirements):
        calls.append(requirements)
        path = tmp_path / f"overlay-{len(calls)}" / "py"
        path.mkdir(parents=True)
        return SimpleNamespace(status="ready", path=str(path), error_log=None)

    class FakeSession:
        pass

    monkeypatch.setattr(runner, "ensure_overlay", fake_ensure)
    session = FakeSession()
    first_workflow = {
        "__meta__": {"settings": {"code_requirements": "pandas==2.2.0"}},
    }
    changed_workflow = {
        "__meta__": {"settings": {"code_requirements": "pandas==2.2.1"}},
    }

    await runner.prepare_code_pythonpath(first_workflow, session=session)
    await runner.prepare_code_pythonpath(changed_workflow, session=session)
    await runner.prepare_code_pythonpath(changed_workflow, session=session)

    assert calls == ["pandas==2.2.0", "pandas==2.2.1"]


@pytest.mark.asyncio
async def test_plain_chat_session_initialization_does_not_prepare_requirements(
    monkeypatch, tmp_path,
):
    """Constructing/prewarming a real Chat session must not touch Workflow deps."""
    from vibecanvas_api.services import workflow_sandbox_runner as runner
    from vibecanvas_api.services.sandbox.manager import SandboxSession

    async def forbidden(_requirements):
        raise AssertionError("plain Chat must not prepare code_requirements")

    monkeypatch.setattr(runner, "ensure_overlay", forbidden)

    run_dir = tmp_path / "chat"
    run_dir.mkdir()
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat-scope",
        run_dir=str(run_dir),
        overlay_dir=None,
        provider=object(),
        base_binds=[],
        expose_run=False,
    )
    prewarmed = []

    async def fake_fileop_pool():
        prewarmed.append(True)
        return object()

    monkeypatch.setattr(session, "_get_fileop_pool", fake_fileop_pool)
    await session.prewarm_fileops()

    assert prewarmed == [True]
    assert session._workflow_dependency_key is None
    assert session._workflow_dependency_pythonpath is None


@pytest.mark.asyncio
async def test_prepare_requirements_failure_is_clear(monkeypatch):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    async def fake_ensure(_requirements):
        return SimpleNamespace(
            status="failed", path=None, error_log="no compatible wheel",
        )

    monkeypatch.setattr(runner, "ensure_overlay", fake_ensure)
    with pytest.raises(runner.WorkflowSandboxRunError, match="no compatible wheel"):
        await runner.prepare_code_pythonpath({
            "__meta__": {"settings": {"code_requirements": "private-pkg==1"}}
        }, session=SimpleNamespace(remote=False))


@pytest.mark.asyncio
async def test_noninteractive_execution_self_heals_cold_requirements(
    monkeypatch, tmp_path,
):
    """Deployment/Task execution uses the same idempotent overlay builder."""
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    overlay = tmp_path / "overlays" / "key" / "py"
    overlay.mkdir(parents=True)
    calls = []

    async def fake_ensure(requirements):
        calls.append(requirements)
        return SimpleNamespace(
            status="ready", path=str(overlay), error_log=None,
        )

    monkeypatch.setattr(runner, "ensure_overlay", fake_ensure)
    path = await runner.ensure_code_pythonpath({
        "__meta__": {
            "settings": {"code_requirements": "  requests==2.32.5  "},
        },
    }, session=SimpleNamespace(remote=False))

    assert path == str(overlay)
    assert calls == ["requests==2.32.5"]


@pytest.mark.asyncio
async def test_remote_dependency_preparation_is_owned_by_sandboxd(monkeypatch):
    """API/worker proxies never require a local package installer."""
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    calls = []

    class FakeManager:
        async def ensure_workflow_dependencies(self, requirements):
            calls.append(requirements)
            return {
                "status": "ready",
                "path": "/sandboxd/lib-overlay/key/py",
                "error_log": None,
            }

    async def local_build_forbidden(_requirements):
        raise AssertionError("remote execution must not run pip in API/worker")

    monkeypatch.setattr(runner, "ensure_overlay", local_build_forbidden)
    path = await runner.ensure_code_pythonpath(
        {
            "__meta__": {
                "settings": {"code_requirements": "requests==2.32.5"},
            },
        },
        session=SimpleNamespace(remote=True, _manager=FakeManager()),
    )

    assert path == "/sandboxd/lib-overlay/key/py"
    assert calls == ["requests==2.32.5"]


def test_merge_stage_extra_preserves_credentials(tmp_path):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    exec_dir = tmp_path / "__exec__"
    exec_dir.mkdir()
    (exec_dir / "extra.json").write_text(
        json.dumps({"llm_credentials": {"model": {"api_key": "secret"}}}),
        encoding="utf-8",
    )

    runner._merge_stage_extra(str(tmp_path), code_pythonpath="/cache/key/py")

    merged = json.loads((exec_dir / "extra.json").read_text(encoding="utf-8"))
    assert merged == {
        "llm_credentials": {"model": {"api_key": "secret"}},
        "code_pythonpath": "/cache/key/py",
    }

from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from vibecanvas_api.services.sandbox.manager import SandboxSession


@pytest.mark.asyncio
async def test_vfs_hydration_isolates_folder_transactions_and_skips_unsafe_paths(
    tmp_path, monkeypatch,
) -> None:
    from vibecanvas_api.services.sandbox import manager as manager_module

    opened: list[int] = []
    queried: list[tuple[int, str]] = []

    @asynccontextmanager
    async def fake_session_scope(*, tenant_id: str):
        assert tenant_id == "tenant-a"
        index = len(opened)
        opened.append(index)
        yield SimpleNamespace(index=index)

    class FakeRepo:
        def __init__(self, session, *, object_store) -> None:
            del object_store
            self.index = session.index

        async def ls(self, *, wf_id: str, prefix: str):
            assert wf_id == "workflow-1"
            queried.append((self.index, prefix))
            if self.index == 0:
                raise RuntimeError("simulated failed transaction")
            if prefix == "/memory/":
                return [SimpleNamespace(path="/memory/../escaped.txt")]
            return []

        async def read_bytes(self, **_kwargs):
            raise AssertionError("unsafe paths must be rejected before object read")

    monkeypatch.setattr(manager_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(manager_module, "VfsRepo", FakeRepo)
    monkeypatch.setattr(manager_module, "get_object_store", lambda: object())

    written = await manager_module._hydrate_run_folders(
        str(tmp_path / "run"), "workflow-1", "tenant-a"
    )

    assert written == 0
    assert opened == [0, 1, 2]
    assert queried == [
        (0, "/data/"),
        (1, "/memory/"),
        (2, "/logs/"),
    ]
    assert not (tmp_path / "run" / "escaped.txt").exists()


@pytest.mark.asyncio
async def test_workflow_job_is_staged_and_collected_by_owning_daemon(
    tmp_path, monkeypatch,
) -> None:
    runs_root = tmp_path / "runs"
    session_root = runs_root / "workspace"
    session_root.mkdir(parents=True)
    session = SandboxSession(
        tenant_id="tenant-a",
        wf_id="batch-task",
        run_dir=str(session_root),
        overlay_dir=None,
        provider=object(),
        base_binds=[],
        expose_run=True,
    )

    async def submit(job: dict, *, timeout: float) -> dict:
        assert timeout == 15.0
        result_dir = runs_root / job["run_subpath"] / "__exec__"
        assert json.loads((result_dir / "job.json").read_text()) == {
            "kind": "workflow"
        }
        assert json.loads((result_dir / "workflow.json").read_text()) == {
            "node": {"node_type": "CodeNode"}
        }
        assert json.loads((result_dir / "inputs.json").read_text()) == {
            "value": 3
        }
        (result_dir / "result.json").write_text(
            json.dumps({"final_outputs": {"node": {"value": 6}}}),
            encoding="utf-8",
        )
        return {"status": "success"}

    monkeypatch.setattr(session, "submit_sandbox_job", submit)
    outcome = await session.execute_workflow_job(
        workflow={"node": {"node_type": "CodeNode"}},
        inputs={"value": 3},
        extra={"runtime": "test"},
        tenant="tenant-a",
        run_id="row-1",
        run_subpath="batch/jobs/000001",
        timeout=15.0,
    )

    assert outcome["status"] == {"status": "success"}
    assert outcome["result"]["final_outputs"]["node"] == {"value": 6}


@pytest.mark.asyncio
async def test_workflow_job_rejects_cross_tenant_and_traversal(tmp_path) -> None:
    session_root = tmp_path / "workspace"
    session_root.mkdir()
    session = SandboxSession(
        tenant_id="tenant-a",
        wf_id="batch-task",
        run_dir=str(session_root),
        overlay_dir=None,
        provider=object(),
        base_binds=[],
        expose_run=True,
    )
    common = {
        "workflow": {},
        "inputs": {},
        "extra": None,
        "run_id": "row-1",
    }
    with pytest.raises(ValueError, match="tenant"):
        await session.execute_workflow_job(
            **common,
            tenant="tenant-b",
            run_subpath="batch/jobs/000001",
        )
    with pytest.raises(ValueError, match="subpath"):
        await session.execute_workflow_job(
            **common,
            tenant="tenant-a",
            run_subpath="../escape",
        )


@pytest.mark.asyncio
async def test_object_backed_session_uses_private_logical_mount_projection(
    tmp_path, monkeypatch,
) -> None:
    from vibecanvas_api.services.sandbox import manager as manager_module

    hydrated = tmp_path / "opaque-s3-download"
    hydrated.mkdir()
    (hydrated / "existing.txt").write_text("from-object-store")

    class ObjectBackedStore:
        pass

    async def no_hydrate(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(
        manager_module,
        "build_run_context",
        lambda _run_id, _tenant_id: {"run_dir": str(hydrated)},
    )
    monkeypatch.setattr(manager_module, "get_object_store", ObjectBackedStore)
    monkeypatch.setattr(manager_module, "_hydrate_run_folders", no_hydrate)
    monkeypatch.setattr(manager_module, "get_sandbox_provider", lambda: object())
    monkeypatch.setattr(manager_module, "_workflow_python_binds", lambda: [])

    manager = manager_module.SandboxManager(max_resident=2, idle_ttl_s=60)
    session = await manager._build_session("tenant-a", "workflow-1")
    try:
        assert session.pool_runs_root is not None
        assert session.workflow_run_dir.endswith("/runs/workflow-1")
        assert session.workflow_run_dir.startswith(
            session.materialized_projection_root
        )
        assert (
            session.workflow_run_dir
            == f"{session.pool_runs_root}/workflow-1"
        )
        assert (
            Path(session.workflow_run_dir, "existing.txt").read_text()
            == "from-object-store"
        )
    finally:
        shutil.rmtree(session.materialized_projection_root, ignore_errors=True)


def test_sync_workflow_runner_delegates_logical_job_to_sandbox_service(
    monkeypatch,
) -> None:
    from vibecanvas_api.services import workflow_runner

    captured: dict = {}

    class Manager:
        async def run_workflow_once(self, **kwargs):
            captured.update(kwargs)
            return {
                "final_outputs": {"end": {"ok": True}},
                "error_dict": {},
                "execution_time": 1.25,
            }

    monkeypatch.setattr(workflow_runner.config, "sandbox_service_mode", "service")
    monkeypatch.setattr(workflow_runner, "get_sandbox_manager", lambda: Manager())
    monkeypatch.setattr(
        workflow_runner,
        "inject_into_run_context_sync",
        lambda *_args, **_kwargs: {
            "llm_credentials": {
                "managed": {"api_key": "short-lived-broker-capability"}
            }
        },
    )
    monkeypatch.setattr(
        workflow_runner,
        "compute_allow_hosts",
        lambda *_args, **_kwargs: {"models.example.test"},
    )
    monkeypatch.setattr(
        workflow_runner,
        "get_sandbox_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("the API process must not own the provider")
        ),
    )

    workflow = {
        "__meta__": {"settings": {"code_requirements": "httpx==0.28.1"}},
        "prompt": {"node_type": "PromptNode"},
    }
    outputs, errors, elapsed = workflow_runner.run_workflow_sandboxed_sync(
        workflow_id="workflow-1",
        workflow_dict=workflow,
        inputs={"query": "hello"},
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-1",
    )

    assert outputs == {"end": {"ok": True}}
    assert errors == {}
    assert elapsed == 1.25
    assert captured == {
        "workflow_id": "workflow-1",
        "workflow": workflow,
        "inputs": {"query": "hello"},
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "run_id": "run-1",
        "extra": {
            "llm_credentials": {
                "managed": {"api_key": "short-lived-broker-capability"}
            }
        },
        "allow_hosts": ["models.example.test"],
        "requirements": "httpx==0.28.1",
    }

"""Security regression: workflow sandboxes never receive host credentials."""

from __future__ import annotations

import json
import os
import sys

import pytest

from vibecanvas_api.services.object_store import FilesystemObjectStore
from vibecanvas_api.services.sandbox import EngineNeedsHostNode, EngineRunResult
from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider
from vibecanvas_api.services.sandbox.provider import SandboxResult
from vibecanvas_api.services.sandbox.warm import WarmGvisorPool


def _meta() -> dict:
    return {
        "workflow_id": "wf-no-db",
        "workflow_name": "no_db",
        "workflow_version": 1,
        "workflow_subversion": 0,
    }


def _pure_workflow() -> dict:
    return {
        "__meta__": _meta(),
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {},
            "output_fields": {},
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {},
            "output_fields": {},
            "node_config": {},
            "children": [],
        },
    }


def _removed_kb_workflow() -> dict:
    workflow = _pure_workflow()
    workflow["node_kb"] = {"node_type": "KnowledgeSearchNode"}
    return workflow


def _capture_provider(captured: dict) -> RootlessGvisorProvider:
    provider = RootlessGvisorProvider("/nonexistent/runsc")

    def fake_run(*, run_dir, command, env=None, **kwargs):
        captured["command"] = list(command)
        captured["env"] = dict(env or {})
        captured["kwargs"] = kwargs
        exec_dir = os.path.join(run_dir, "__exec__")
        os.makedirs(exec_dir, exist_ok=True)
        with open(os.path.join(exec_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "final_outputs": {"__end__": {}},
                    "error_dict": {},
                    "execution_time": 0.01,
                },
                f,
            )
        return SandboxResult(exit_code=0, stdout="", stderr="", duration_s=0.01)

    provider.run = fake_run  # type: ignore[method-assign]
    return provider


def test_workflow_uses_engine_entrypoint_without_host_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secret@db/product")
    monkeypatch.setenv(
        "AGENT_RUNTIME_DATABASE_URL", "postgresql://runtime:secret@db/state"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-host-secret")
    monkeypatch.setenv("KMS_LOCAL_MASTER_KEY", "host-kms-key")
    captured: dict = {}
    provider = _capture_provider(captured)

    result = provider.run_workflow(
        run_dir=str(tmp_path),
        workflow=_pure_workflow(),
        inputs={},
        run_id="run-no-secrets",
        tenant="organization",
    )
    assert isinstance(result, EngineRunResult)
    assert captured["command"] == [
        sys.executable,
        "-m",
        "vibecanvas_engine.sandbox_entry",
        "run-no-secrets",
    ]
    environment = captured["env"]
    for name in (
        "DATABASE_URL",
        "AGENT_RUNTIME_DATABASE_URL",
        "OPENAI_API_KEY",
        "KMS_LOCAL_MASTER_KEY",
        "ADMIN_DATABASE_URL",
        "REDIS_URL",
    ):
        assert name not in environment


def test_removed_database_node_fails_before_sandbox_launch(tmp_path):
    captured: dict = {}
    provider = _capture_provider(captured)
    with pytest.raises(EngineNeedsHostNode, match="KnowledgeSearchNode"):
        provider.run_workflow(
            run_dir=str(tmp_path),
            workflow=_removed_kb_workflow(),
            inputs={},
            run_id="run-kb",
            tenant="organization",
        )
    assert "command" not in captured


def test_warm_pool_does_not_receive_host_credentials(
    tmp_path, monkeypatch
):
    store_root = str(tmp_path / "store")
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.warm.get_object_store",
        lambda: FilesystemObjectStore(root=store_root),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secret@db/product")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-host-secret")
    captured: dict = {}

    class _Process:
        def poll(self):
            return None

    class _Handle:
        proc = _Process()

    class _Provider:
        def run_serve(self, **kwargs):
            captured.update(kwargs)
            return _Handle()

        def stop_serve(self, _handle):
            return None

    pool = WarmGvisorPool(
        provider=_Provider(),
        store_root=store_root,
        work_root=str(tmp_path / "work"),
    )
    pool.start()
    try:
        environment = captured["env"]
        assert "DATABASE_URL" not in environment
        assert "OPENAI_API_KEY" not in environment
        assert captured["command"][1:3] == [
            "-m",
            "vibecanvas_api.sandbox_entry",
        ]
    finally:
        pool.stop()

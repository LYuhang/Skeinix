# -*- coding: utf-8 -*-
"""FU-1: run_workflow uses the shared resident workflow sandbox runner."""
import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_run_workflow_uses_resident_runner_stream(monkeypatch, tmp_path):
    import importlib
    # run/__init__ rebinds the name ``run_workflow`` to the tool object, so import
    # the module explicitly to monkeypatch its module-level helpers.
    rw = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.run_workflow")
    run_dir = tmp_path / "tenant" / "run123"
    run_dir.mkdir(parents=True)

    seen = {}

    class FakeSession:
        def __init__(self):
            self.run_dir = str(run_dir)
            self.workflow_run_dir = str(run_dir)
            self.workflow_run_id = "wf"

    def fake_run_workflow_once_sync(session, **kwargs):
        seen.update(session=session, **kwargs)
        return SimpleNamespace(result_json={
                "final_outputs": {"__end__": {"answer": 42}, "start": {"x": 1}},
                "error_dict": {},
                "execution_time": 1.234,
        })

    monkeypatch.setattr(rw, "_save_if_dirty", lambda ctx: None)
    monkeypatch.setattr(rw, "_resolve_session_sync", lambda ctx: FakeSession())
    monkeypatch.setattr(rw, "run_workflow_once_sync", fake_run_workflow_once_sync)
    monkeypatch.setattr(rw, "write_node_result_sync", lambda *a, **k: None)

    ctx = MagicMock()
    ctx.workflow = {"n1": {"node_name": "start", "node_type": "start"},
                    "n2": {"node_name": "__end__", "node_type": "end"}}
    ctx.current_workflow_id = "wf"
    ctx.wf_id = "wf"
    ctx.username = "u"
    ctx.tenant_id = "tenant"
    rt = MagicMock()
    rt.context = ctx

    content, artifact = rw._sync_run_workflow("{}", rt)

    # FU-1: the tool delegates to the one shared resident workflow runner.
    assert seen["session"].workflow_run_dir == str(run_dir)
    assert seen["workflow_run_id"] == "wf"
    assert seen["tenant_id"] == "tenant"
    assert seen["install_dependencies"] is True
    # contract preserved: result extracted from result.json. Agent workflow runs
    # do not create or expose workflow-page execution state.
    blob = json.dumps([content, artifact], default=str)
    assert "answer" in blob          # final_outputs surfaced as node_outputs
    assert "1.234" in blob           # execution_time from result.json
    assert "exec_id" not in blob
    assert re.search(r'"e_[0-9a-f]{12}"', blob) is None


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_shared_runner_stages_only_broker_descriptor(monkeypatch, tmp_path):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    run_dir = tmp_path / "workflow-run"
    run_dir.mkdir()
    seen: dict = {}
    broker_entry = {
        "provider": "openai",
        "model_name": "gpt-test",
        "api_url": "http://platform.test/api/internal/runtime-model/v1",
        "api_key": "short-lived-workflow-capability",
        "timeout": 60,
    }

    class FakeSession:
        workflow_run_dir = str(run_dir)
        workflow_run_id = "wf-1"

        async def submit_workflow_stream(self, **kwargs):
            seen.update(kwargs)
            seen["extra_file_exists"] = (run_dir / "__exec__" / "extra.json").exists()
            yield {
                "type": "result",
                "final_outputs": {"end": {"ok": True}},
                "error_dict": {},
                "execution_time": 0.1,
            }

        async def writeback_vfs(self):
            return None

    async def inject(_context, _workflow, tenant_id, **claims):
        assert tenant_id == "org-1"
        assert claims == {
            "user_id": "user-1",
            "workflow_id": "wf-1",
            "execution_id": "execution-1",
            "execution_resource_type": "workflow_execution",
        }
        return {"llm_credentials": {"Saved": broker_entry}}

    monkeypatch.setattr(runner, "inject_into_run_context_async", inject)
    monkeypatch.setattr(
        runner,
        "clear_run_contents",
        lambda *_args, **_kwargs: _async_value(None),
    )
    job = await runner.run_workflow_once(
        FakeSession(),
        tenant_id="org-1",
        workflow={"node_1": {"node_type": "PromptNode"}},
        inputs={"value": 1},
        workflow_run_id="wf-1",
        user_id="user-1",
        workflow_id="wf-1",
        execution_id="execution-1",
        execution_resource_type="workflow_execution",
    )
    assert job.result_json["final_outputs"]["end"]["ok"] is True
    assert seen["extra"] == {
        "llm_credentials": {"Saved": broker_entry},
    }
    assert seen["extra_file_exists"] is False
    staged = json.dumps(seen["extra"])
    assert "provider-secret" not in staged
    assert "provider.example" not in staged


@pytest.mark.asyncio
async def test_terminal_frame_is_visible_only_after_vfs_writeback(tmp_path):
    from vibecanvas_api.services import workflow_sandbox_runner as runner

    run_dir = tmp_path / "workflow-run"
    run_dir.mkdir()
    lifecycle: list[str] = []

    class FakeSession:
        workflow_run_dir = str(run_dir)
        workflow_run_id = "wf-1"

        async def submit_workflow_stream(self, **_kwargs):
            lifecycle.append("result-produced")
            yield {
                "type": "result",
                "final_outputs": {},
                "error_dict": {},
                "execution_time": 0.1,
            }

        async def writeback_vfs(self):
            lifecycle.append("writeback-complete")

    stream = runner.stream_workflow_job(
        stop=asyncio.Event(),
        workflow={},
        inputs={},
        workflow_run_id="wf-1",
        tenant_id="tenant-1",
        session=FakeSession(),
    )
    message = await anext(stream)

    assert message["type"] == "result"
    assert lifecycle == ["result-produced", "writeback-complete"]

    await stream.aclose()
    assert lifecycle.count("writeback-complete") == 1

# -*- coding: utf-8 -*-
"""Current batch_execute JSONL/session-runner contract."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_jsonl_record_preserves_success_and_error_shapes():
    from vibecanvas_api.services.platform_mcp.run_tools._batch_coro import _jsonl_record

    success = _jsonl_record(
        2,
        {"prompt": "hello"},
        {
            "final_outputs": {"__end__": {"answer": "ok"}, "n1": {"x": 1}},
            "error_dict": {},
            "execution_time": 1.23456,
        },
    )
    assert success == {
        "index": 2,
        "status": "success",
        "input": {"prompt": "hello"},
        "output": {"answer": "ok"},
        "node_outputs": {"__end__": {"answer": "ok"}, "n1": {"x": 1}},
        "errors": {},
        "execution_time": 1.235,
    }

    failed = _jsonl_record(3, {"prompt": "bad"}, None, "worker stopped")
    assert failed["status"] == "error"
    assert failed["errors"] == {"_row_error": "worker stopped"}
    assert failed["output"] is None


@pytest.mark.asyncio
async def test_batch_coro_rows_executes_through_daemon_owned_workspace():
    from vibecanvas_api.services.platform_mcp.run_tools import _batch_coro as bc

    class FakeSession:
        def __init__(self):
            self.writes = []
            self.writeback_vfs = AsyncMock()
            self.execute_workflow_job = AsyncMock(
                side_effect=[
                    {
                        "status": {"status": "success"},
                        "result": {
                            "final_outputs": {"__end__": {"answer": "one"}},
                            "error_dict": {},
                            "execution_time": 0.5,
                        },
                    },
                    {
                        "status": {
                            "status": "error",
                            "error_message": "worker stopped",
                        },
                        "result": None,
                    },
                ]
            )

        async def write_file(self, path, content):
            self.writes.append((path, content))
            return {"ok": True, "bytes": len(content)}

    session = FakeSession()
    ctx = SimpleNamespace(tenant_id="tenant-1")
    rows = [{"prompt": "one"}, {"prompt": "two"}]
    with (
        patch(
            "vibecanvas_api.services.platform_mcp.run_tools._backend._save_if_dirty",
            return_value=None,
        ),
    ):
        result = await bc._batch_coro_rows(
            rows, "/run/batch/results.jsonl", {"n1": {}}, ctx, 2, session,
        )

    assert session.execute_workflow_job.await_count == 2
    first = session.execute_workflow_job.await_args_list[0].kwargs
    assert first["inputs"] == {"prompt": "one"}
    assert "run_dir" not in first
    assert result["total"] == 2
    assert result["failed_rows"] == 1
    assert session.writes[0][0] == "/run/batch/results.jsonl"
    records = [json.loads(line) for line in session.writes[0][1].splitlines()]
    assert records[0]["output"] == {"answer": "one"}
    assert records[1]["errors"] == {"_row_error": "worker stopped"}
    session.writeback_vfs.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_execute_waits_for_rows_and_returns_completed_summary():
    from vibecanvas_api.services.platform_mcp.run_tools.batch_execute import _do_batch_execute

    class FakeSession:
        async def read_file(self, path):
            assert path == "/data/input.csv"
            return {
                "ok": True,
                "kind": "text",
                "content": "prompt\nhello\nworld\n",
            }

    ctx = SimpleNamespace(
        username="u",
        wf_id="w",
        current_workflow_id="w",
        chat_id="c",
        tenant_id="tenant-1",
        workflow={},
    )

    async def sandbox_session():
        return FakeSession()

    ctx.sandbox_session = sandbox_session
    runtime = SimpleNamespace(context=ctx)

    with patch(
        "vibecanvas_api.services.platform_mcp.run_tools.batch_execute._batch_coro_rows",
        new=AsyncMock(
            return_value={
                "result_path": "/run/batch/output.jsonl",
                "total": 2,
                "failed_rows": 1,
                "result_summary": "2 rows done, 1 errors",
            }
        ),
    ) as run_rows:
        form_info = await _do_batch_execute.__wrapped__(
            "/data/input.csv", "Test", 2, runtime,
            output_path="/run/batch/output.jsonl",
        )

    run_rows.assert_awaited_once()
    assert form_info["total_rows"] == 2
    assert form_info["failed_rows"] == 1
    assert form_info["status"] == "completed_with_errors"
    assert form_info["output_path"] == "/run/batch/output.jsonl"
    assert "task_id" not in form_info


def test_run_tools_include_batch():
    from vibecanvas_api.services.platform_mcp.run_tools import RUN_TOOLS

    assert "batch_execute" in {tool.name for tool in RUN_TOOLS}

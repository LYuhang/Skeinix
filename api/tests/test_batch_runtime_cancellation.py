"""Soft-cancellation behavior for the shared batch runtime."""
from __future__ import annotations

import threading

from vibecanvas_api.services import batch_runtime


async def test_soft_cancel_wins_at_safe_boundary_and_skips_waiting_rows(monkeypatch):
    stop_event = threading.Event()

    class FakeSession:
        calls = 0

        async def execute_workflow_job(self, **_kwargs):
            self.calls += 1
            stop_event.set()
            return {
                "status": {
                    "status": "error",
                    "error_message": "late business error",
                },
                "result": None,
            }

    class FakeCoordinator:
        def __init__(self):
            self.session = FakeSession()
            self.closed = []

        async def get_session(self, *_args, **_kwargs):
            return self.session

        async def close_session(self, tenant_id, scope_id):
            self.closed.append((tenant_id, scope_id))

    coordinator = FakeCoordinator()
    monkeypatch.setattr(batch_runtime, "get_sandbox_coordinator", lambda: coordinator)

    async def _no_dependency_layer(_workflow):
        return None

    monkeypatch.setattr(batch_runtime, "reuse_code_pythonpath", _no_dependency_layer)

    result = await batch_runtime.run_batch_workflow(
        task_id="task-soft-cancel",
        tenant_id="tenant-1",
        user_id="user-1",
        workflow_id="workflow-1",
        workflow={"__meta__": {"workflow_id": "workflow-1"}},
        rows=[{"value": "first"}, {"value": "waiting"}],
        column_mapping={},
        concurrency=1,
        stop_event=stop_event,
        prepared_run_extra={},
    )

    assert coordinator.session.calls == 1
    assert coordinator.closed == [("tenant-1", "batch-task-soft-cancel")]
    assert result.status == "interrupted"
    assert result.summary["cancelled"] == 2
    assert result.summary["can_resume"] is True
    assert [row["status"] for row in result.rows] == ["cancelled", "cancelled"]
    assert all("late business error" not in str(row) for row in result.rows)

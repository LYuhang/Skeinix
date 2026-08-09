from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from vibecanvas_api.services.platform_mcp.plan_tools import PLAN_TOOLS
from vibecanvas_api.services.platform_mcp.plan_tools.create_execution_plan import (
    _do_create_execution_plan,
)


class FakeVfs:
    def __init__(self, data: bytes | None):
        self.data = data
        self.reads: list[tuple[str, str]] = []

    def read_bytes(self, *, wf_id: str, path: str):
        self.reads.append((wf_id, path))
        return self.data


class FakeLoadedSession:
    def __init__(self, vfs: FakeVfs, data: bytes):
        self.vfs = vfs
        self.data = data
        self.flushes = 0

    async def writeback_vfs(self):
        self.flushes += 1
        self.vfs.data = self.data


class FakeManager:
    def __init__(self, loaded):
        self.loaded = loaded
        self.lookups: list[tuple[str, str]] = []

    async def get_loaded_session(self, tenant_id: str, wf_id: str):
        self.lookups.append((tenant_id, wf_id))
        return self.loaded


def _runtime(data: bytes | None):
    return SimpleNamespace(context=SimpleNamespace(
        runtime_location="platform_mcp",
        tenant_id="11111111-1111-1111-1111-111111111111",
        username="22222222-2222-2222-2222-222222222222",
        chat_id="chat-1",
        turn_id="turn-1",
        wf_id="workspace-1",
        approval_mode="agent",
        authorization_generation="generation",
        authorization_membership_id="33333333-3333-3333-3333-333333333333",
        authorization_membership_role="member",
        authorization_session_generation=2,
        vfs=FakeVfs(data),
    ))


@pytest.mark.asyncio
async def test_invalid_path_is_rejected_before_vfs_read() -> None:
    runtime = _runtime(b"secret")
    content, artifact = await _do_create_execution_plan("/memory/secret", runtime)
    assert json.loads(content)["status"] == "invalid"
    assert runtime.context.vfs.reads == []
    assert artifact["status"] == "success"


@pytest.mark.asyncio
async def test_invalid_plan_has_repair_report_and_no_db_session(monkeypatch) -> None:
    runtime = _runtime(b'{"schema_version":1}')

    @asynccontextmanager
    async def forbidden_session(**_kwargs):
        raise AssertionError("invalid plan opened a database transaction")
        yield

    module = __import__(
        "vibecanvas_api.services.platform_mcp.plan_tools.create_execution_plan",
        fromlist=["session_scope"],
    )
    monkeypatch.setattr(module, "session_scope", forbidden_session)
    content, _artifact = await _do_create_execution_plan(
        "/data/plans/research.plan.json", runtime,
    )
    parsed = json.loads(content)
    assert parsed["status"] == "invalid"
    assert parsed["errors"]


@pytest.mark.asyncio
async def test_plan_flushes_loaded_workspace_before_durable_read(monkeypatch) -> None:
    definition = {
        "schema_version": 1,
        "title": "One step",
        "nodes": [
            {"id": "start", "type": "start", "next": ["work"]},
            {
                "id": "work",
                "type": "subagent",
                "title": "Work",
                "task": "Write /data/result.txt and return that path.",
                "next": ["end"],
            },
            {"id": "end", "type": "end"},
        ],
        "budgets": {"max_wall_time_seconds": 300},
    }
    runtime = _runtime(None)
    loaded = FakeLoadedSession(
        runtime.context.vfs,
        json.dumps(definition).encode("utf-8"),
    )
    manager = FakeManager(loaded)
    module = __import__(
        "vibecanvas_api.services.platform_mcp.plan_tools.create_execution_plan",
        fromlist=["get_existing_sandbox_manager"],
    )
    monkeypatch.setattr(module, "get_existing_sandbox_manager", lambda: manager)

    @asynccontextmanager
    async def stop_before_persistence(**_kwargs):
        raise RuntimeError("durable read succeeded")
        yield

    monkeypatch.setattr(module, "session_scope", stop_before_persistence)
    _content, artifact = await _do_create_execution_plan(
        "/data/plans/research.plan.json",
        runtime,
    )

    assert loaded.flushes == 1
    assert manager.lookups == [
        (runtime.context.tenant_id, runtime.context.wf_id),
    ]
    assert runtime.context.vfs.reads == [
        (runtime.context.wf_id, "/data/plans/research.plan.json"),
    ]
    assert artifact["status"] == "error"


def test_plan_mcp_surface_has_exactly_one_tool() -> None:
    assert [item.name for item in PLAN_TOOLS] == ["create_execution_plan"]

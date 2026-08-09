"""F3 (F-T5) — node_execute / run_workflow share the resident session's live FS.

When ``ctx.sandbox_session()`` resolves a session, the engine run must execute
against the SAME
live FS the session + the F1 FS tools use — so a file the agent just wrote is
visible to the run, and writeback is unified through the session (not a fresh,
independent ``build_run_context`` staging dir).

When ``ctx.sandbox_session()`` is None (the plain-chat / no-sandbox path), the
engine run keeps today's behavior: no run_context substitution, and the tool
never builds (nor releases) a separate run workspace.
"""
import asyncio
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_engine.nodes.base import BaseNode
from vibecanvas_engine.register import node_registry
from vibecanvas_engine.utils import safe_call_with_args
from vibecanvas_api.storage.vfs_store import PostgresVfsStore
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.sync_session import current_sync_tenant_id
from vibecanvas_api.storage.workflow_repo import WorkflowRepo
from vibecanvas_api.services.object_store import FilesystemObjectStore


class _SessFsEchoNode(BaseNode):
    NODE_TYPE = "SessFsEchoTestNode"

    @safe_call_with_args(prefix="[SessFsEcho]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        # Surface the run_context that reached the node so the test can assert
        # the session's dirs were threaded through (extra carries run_context).
        return {"echoed": inputs,
                "run_dir": (extra or {}).get("run_dir"),
                "data_dir": (extra or {}).get("data_dir")}


class _SessFsStartNode(BaseNode):
    NODE_TYPE = "SessFsStartTestNode"

    @safe_call_with_args(prefix="[SessFsStart]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        return {"ran": True}


node_registry._module_dict.setdefault("SessFsEchoTestNode", _SessFsEchoNode)
node_registry._module_dict.setdefault("SessFsStartTestNode", _SessFsStartNode)


class _FakeSession:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.workflow_run_dir = run_dir
        self.workflow_run_id = "wf_sfs"


class _Repo:
    def __init__(self, wf): self._wf = wf
    def get_current_workflow(self, wf_id): return self._wf
    def get_meta(self, wf_id): return {"wf_id": "wf_sfs", "active_major": 1, "active_sub": 0}


class _Ctx:
    def __init__(self, vfs, wf, session, tenant_id, username="u"):
        self.vfs = vfs
        self.repo = _Repo(wf)
        self.wf_id = "wf_sfs"
        self.tenant_id = tenant_id
        self.chat_id = "chat_sfs"
        self.username = username
        self.workflow = wf
        self.workflow_dirty = False
        self._session = session

    async def sandbox_session(self):
        if self._session is None:
            raise ValueError("no session")
        return self._session


class _CtxNoAccessor:
    """A context WITHOUT a sandbox_session accessor at all (older shape)."""
    def __init__(self, vfs, wf, username="u"):
        self.vfs = vfs
        self.repo = _Repo(wf)
        self.wf_id = "wf_sfs"
        self.chat_id = "chat_sfs"
        self.username = username
        self.workflow = wf
        self.workflow_dirty = False


class _Rt:
    def __init__(self, ctx):
        self.context = ctx


def _node(node_type, node_id="node_2", node_name="task", children=None, input_fields=None):
    return {"node_type": node_type, "node_id": node_id, "node_name": node_name,
            "node_description": "", "input_fields": input_fields or {},
            "output_fields": {}, "node_config": {}, "children": children or [],
            "__attributes__": {}}


def _wf_single(node2):
    return {"node_2": node2,
            "__meta__": {"workflow_id": "wf_sfs", "workflow_version": 1, "workflow_subversion": 0}}


def _wf_runnable():
    # A minimal StartNode-only graph that Workflow() accepts and trigger() runs.
    start = _node("StartNode", node_id="node_1", node_name="start")
    return {"node_1": start,
            "__meta__": {"workflow_id": "wf_sfs", "workflow_version": 1, "workflow_subversion": 0}}


async def _seed(app_engine, tenant, wf_id, chat_id, user):
    async with app_engine.begin() as c:
        await c.execute(text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'x')"), {"t": tenant})
        await c.execute(text("INSERT INTO users(user_id,tenant_id,email) VALUES (:u,:t,:e)"),
                        {"u": user, "t": tenant, "e": f"{user.hex[:6]}@example.com"})
    async with session_scope(tenant_id=str(tenant)) as session:
        await WorkflowRepo(session, str(user)).create_workflow(
            wf_id=wf_id,
            name="W",
        )
        await ChatRepo(session, str(user)).register_session(
            wf_id,
            name="Chat",
            chat_id=chat_id,
        )


# ---------------------------------------------------------------------------
# node_execute
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_execute_runs_in_sandbox_with_shared_session_dirs(app_engine, tmp_path, monkeypatch):
    """A node-debug runs in the workflow sandbox via the provider's run_node,
    using the attached session's execution-local run directory."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)

    run_dir = str(tmp_path / "session_run")
    session = _FakeSession(run_dir)
    ctx = _Ctx(PostgresVfsStore(), _wf_single(_node("SessFsEchoTestNode")), session, str(t))
    rt = _Rt(ctx)

    captured = {}

    async def _fake_run_node_once(session, *, tenant_id, node, inputs,
                                  workflow_run_id, **kw):
        captured.update(
            session=session, inputs=inputs, run_id=workflow_run_id, tenant=tenant_id)
        return SimpleNamespace(result_json={
            "final_outputs": {"node_2": {"ok": True}},
            "error_dict": {},
            "execution_time": 0.0,
        })

    monkeypatch.setattr(ct, "run_node_once", _fake_run_node_once)

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, _artifact = await ct._node_execute("node_2", "", rt)
        data = json.loads(content)
        assert data["status"] == "success"
        assert data["output"] == {"ok": True}
    finally:
        current_sync_tenant_id.reset(tok)

    assert captured["session"] is session
    assert captured["run_id"] == "wf_sfs"
    assert captured["tenant"] == str(t)


@pytest.mark.asyncio
async def test_node_execute_no_session_errors(app_engine, monkeypatch):
    """Running a node REQUIRES the workflow sandbox (no in-process fallback): no
    resident session → agent-facing error, no execution sandbox started."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)
    rt = _Rt(_CtxNoAccessor(PostgresVfsStore(), _wf_single(_node("SessFsEchoTestNode"))))

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, artifact = await ct._node_execute("node_2", "", rt)
    finally:
        current_sync_tenant_id.reset(tok)
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "no_workspace"


@pytest.mark.asyncio
async def test_node_execute_session_resolution_failure_errors(app_engine, monkeypatch):
    """A sandbox_session() that raises is treated as no session → the run errors
    (no in-process fallback), never a silent host run."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)
    ctx = _Ctx(PostgresVfsStore(), _wf_single(_node("SessFsEchoTestNode")), None, str(t))
    rt = _Rt(ctx)

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, artifact = await ct._node_execute("node_2", "", rt)
    finally:
        current_sync_tenant_id.reset(tok)
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "no_workspace"


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_runs_in_sandbox_with_shared_session_dirs(app_engine, tmp_path, monkeypatch):
    """A run with a session executes in the workflow sandbox using that
    session's execution-local run directory."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.run_workflow")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)

    run_dir = str(tmp_path / "session_run")
    session = _FakeSession(run_dir)
    ctx = _Ctx(PostgresVfsStore(), _wf_runnable(), session, str(t), username=str(u))
    rt = _Rt(ctx)

    # Capture what the shared workflow sandbox runner is asked to run.
    captured = {}

    def _fake_run_workflow_once_sync(session, *, tenant_id, workflow, inputs,
                                     workflow_run_id, **kw):
        captured.update(
            session=session, workflow=workflow, inputs=inputs,
            run_id=workflow_run_id, tenant=tenant_id)
        return SimpleNamespace(result_json={
            "final_outputs": {},
            "error_dict": {},
            "execution_time": 0.0,
        })

    monkeypatch.setattr(ct, "run_workflow_once_sync", _fake_run_workflow_once_sync)

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, _artifact = await asyncio.to_thread(ct._sync_run_workflow, "{}", rt)
        data = json.loads(content)
        assert data["status"] == "success"
    finally:
        current_sync_tenant_id.reset(tok)

    assert captured["session"] is session
    assert captured["run_id"] == "wf_sfs"
    assert captured["tenant"] == str(t)


@pytest.mark.asyncio
async def test_run_workflow_no_session_errors(app_engine, monkeypatch):
    """Running a workflow REQUIRES the workflow sandbox (no in-process fallback,
    per the locked architecture): with no resident session the tool returns an
    agent-facing error and never starts an execution sandbox."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.run_workflow")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)
    rt = _Rt(_CtxNoAccessor(PostgresVfsStore(), _wf_runnable(), username=str(u)))

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, artifact = await asyncio.to_thread(ct._sync_run_workflow, "{}", rt)
    finally:
        current_sync_tenant_id.reset(tok)

    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "no_workspace"


# ---------------------------------------------------------------------------
# node_execute persists into the workflow's fixed run-tier.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_execute_overwrites_node_in_latest_run(app_engine, tmp_path, monkeypatch):
    """A node_execute persists /run/__exec__/nodes/{node}.json into the
    workflow's fixed run-tier (run_id == wf_id), overwriting only that one
    node's file."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    import vibecanvas_api.services.node_results as nr
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)

    store = FilesystemObjectStore(root=str(tmp_path / "blobs"))
    monkeypatch.setattr(nr, "get_object_store", lambda: store)

    async def _fake_run_node_once(session, *, tenant_id, node, inputs,
                                  workflow_run_id, **kw):
        return SimpleNamespace(result_json={
            "final_outputs": {"node_2": {"ok": 7}},
            "error_dict": {},
            "execution_time": 0.0,
        })

    monkeypatch.setattr(ct, "run_node_once", _fake_run_node_once)

    session = _FakeSession(str(tmp_path / "run"))
    ctx = _Ctx(PostgresVfsStore(), _wf_single(_node("SessFsEchoTestNode")),
               session, str(t), username=str(u))
    rt = _Rt(ctx)

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, _artifact = await ct._node_execute("node_2", "", rt)
        assert json.loads(content)["status"] == "success"
        got = await nr.read_node_result("wf_sfs", str(t), "node_2")
    finally:
        current_sync_tenant_id.reset(tok)

    assert got is not None
    assert got["node_id"] == "node_2"
    assert got["output"] == {"ok": 7}
    assert got["status"] == "completed"


@pytest.mark.asyncio
async def test_node_execute_without_prior_run_persists_to_workflow_run(app_engine, tmp_path, monkeypatch):
    """A node-debug run writes directly to the workflow's fixed run-tier even
    when no previous whole-workflow run exists."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    import vibecanvas_api.services.node_results as nr
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)
    session = _FakeSession(str(tmp_path / "run"))
    ctx = _Ctx(PostgresVfsStore(), _wf_single(_node("SessFsEchoTestNode")),
               session, str(t), username=str(u))
    rt = _Rt(ctx)

    store = FilesystemObjectStore(root=str(tmp_path / "blobs"))
    monkeypatch.setattr(nr, "get_object_store", lambda: store)

    async def _fake_run_node_once(session, *, tenant_id, node, inputs,
                                  workflow_run_id, **kw):
        return SimpleNamespace(result_json={
            "final_outputs": {"node_2": {"ok": 1}},
            "error_dict": {},
            "execution_time": 0.0,
        })

    monkeypatch.setattr(ct, "run_node_once", _fake_run_node_once)

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, _artifact = await ct._node_execute("node_2", "", rt)
        assert json.loads(content)["status"] == "success"
        written = await nr.read_node_result("wf_sfs", str(t), "node_2")
    finally:
        current_sync_tenant_id.reset(tok)

    assert written is not None
    assert written["output"] == {"ok": 1}


@pytest.mark.asyncio
async def test_run_workflow_writes_per_node_run_files(app_engine, tmp_path, monkeypatch):
    """run_workflow with a session writes one /run/__exec__/nodes/{node_id}.json
    per node in previous_outputs (mapped node_name→node_id), status reflecting
    error_dict."""
    import importlib; ct = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.run_workflow")
    import vibecanvas_api.services.node_results as nr
    import vibecanvas_api.storage.vfs_run_repo as vrr
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_sfs", "chat_sfs", u)

    # A 2-node workflow so we can map node_name → node_id.
    wf = {
        "node_1": _node("StartNode", node_id="node_1", node_name="start"),
        "node_2": _node("SessFsEchoTestNode", node_id="node_2", node_name="task"),
        "__meta__": {"workflow_id": "wf_sfs", "workflow_version": 1, "workflow_subversion": 0},
    }
    session = _FakeSession(str(tmp_path / "run"))
    ctx = _Ctx(PostgresVfsStore(), wf, session, str(t), username=str(u))
    rt = _Rt(ctx)

    # Both read (nr) and the sync writer (vrr.PostgresVfsRunStore) must hit the
    # same object store.
    store = FilesystemObjectStore(root=str(tmp_path / "blobs"))
    monkeypatch.setattr(nr, "get_object_store", lambda: store)
    monkeypatch.setattr(vrr, "get_object_store", lambda: store)

    def _fake_run_workflow_once_sync(session, *, tenant_id, workflow, inputs,
                                     workflow_run_id, **kw):
        # node "task" errored; "start" + a synthetic "__start__" channel ok.
        return SimpleNamespace(result_json={
            "final_outputs": {"start": {"ok": True}, "task": {"v": 1}, "__start__": {}},
            "error_dict": {"task": "boom"},
            "execution_time": 0.2,
        })

    monkeypatch.setattr(ct, "run_workflow_once_sync", _fake_run_workflow_once_sync)

    tok = current_sync_tenant_id.set(str(t))
    try:
        content, _artifact = await asyncio.to_thread(ct._sync_run_workflow, "{}", rt)
        data = json.loads(content)
        assert data["status"] == "partial_error"
        # Agent workflow runs write per-node files into the fixed workflow run
        # scope and do not create workflow-page execution state.
        node1 = await nr.read_node_result("wf_sfs", str(t), "node_1")
        node2 = await nr.read_node_result("wf_sfs", str(t), "node_2")
    finally:
        current_sync_tenant_id.reset(tok)

    assert node1 is not None and node1["status"] == "completed"
    assert node1["output"] == {"ok": True}
    assert node2 is not None and node2["status"] == "error"
    assert node2["error"] == "boom"
    assert node2["output"] == {"v": 1}

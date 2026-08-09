"""node_execute agent tool."""
import asyncio
import json
import os
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


class _ApiEchoNode(BaseNode):
    NODE_TYPE = "ApiEchoTestNode"

    @safe_call_with_args(prefix="[ApiEcho]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        return {"echoed": inputs}


class _ApiBoomNode(BaseNode):
    NODE_TYPE = "ApiBoomTestNode"

    @safe_call_with_args(prefix="[ApiBoom]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        raise ValueError("kaboom")


node_registry._module_dict.setdefault("ApiEchoTestNode", _ApiEchoNode)
node_registry._module_dict.setdefault("ApiBoomTestNode", _ApiBoomNode)


class _Repo:
    def __init__(self, wf): self._wf = wf
    def get_current_workflow(self, wf_id): return self._wf
    def get_meta(self, wf_id): return {"wf_id": "wf_b", "active_major": 1, "active_sub": 0}


class _FakeSession:
    def __init__(self, run_dir, _legacy_mount=None, *, final_outputs=None, error_dict=None):
        self.run_dir = run_dir
        self.workflow_run_dir = run_dir
        self.workflow_run_id = "wf_b"
        self.runs_root = os.path.dirname(run_dir)
        self.final_outputs = final_outputs or {}
        self.error_dict = error_dict or {}
        self.captured = {}
        self.captured_jobs = []

    async def submit_sandbox_job(self, job, timeout=600.0):
        exec_dir = os.path.join(self.runs_root, job["run_subpath"], "__exec__")
        with open(f"{exec_dir}/job.json", "r", encoding="utf-8") as f:
            staged = json.load(f)
        self.captured.update(job=job, staged=staged, timeout=timeout)
        self.captured_jobs.append({"job": dict(job), "staged": staged})
        with open(f"{exec_dir}/result.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "final_outputs": self.final_outputs,
                    "error_dict": self.error_dict,
                    "execution_time": 0.0,
                },
                f,
            )
        return {"status": "success"}

    async def writeback_vfs(self):
        return None


class _Ctx:
    def __init__(self, vfs, wf, session=None, tenant_id=None):
        self.repo = _Repo(wf); self.vfs = vfs
        self.wf_id = "wf_b"; self.chat_id = "chat_b"; self.username = "u"
        self.workflow = wf
        self.tenant_id = tenant_id
        self._session = session

    async def sandbox_session(self):
        if self._session is None:
            raise ValueError("no session")
        return self._session


class _Rt:
    def __init__(self, vfs, wf, session=None, tenant_id=None):
        self.context = _Ctx(vfs, wf, session, tenant_id)


def _node(node_type, input_fields=None):
    return {"node_type": node_type, "node_id": "node_2", "node_name": "task",
            "node_description": "", "input_fields": input_fields or {},
            "output_fields": {}, "node_config": {}, "children": [], "__attributes__": {}}


def _wf(node2):
    return {"node_2": node2,
            "__meta__": {"workflow_id": "wf_b", "workflow_version": 1, "workflow_subversion": 0}}


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


def _ca(out):
    """Unpack a tool's (content, artifact) pair; parse content JSON when possible."""
    content, artifact = out
    try:
        body = json.loads(content)
    except (ValueError, TypeError):
        body = content
    return body, artifact


@pytest.mark.asyncio
async def test_node_execute_runs_node_with_configured_values(app_engine, tmp_path, monkeypatch):
    import importlib; ne = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    wf = _wf(_node("ApiEchoTestNode", {"q": {"type": "string", "value": "hi", "reference": ""}}))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    session = _FakeSession(
        str(run_dir),
        str(tmp_path / "store"),
        final_outputs={"node_2": {"echoed": {"q": "hi"}}},
    )
    rt = _Rt(PostgresVfsStore(), wf, session=session, tenant_id=str(t))
    tok = current_sync_tenant_id.set(str(t))
    try:
        body, art = _ca(await ne._node_execute("node_2", "", rt))
        assert art["status"] == "success"                       # tool-level
        assert body["status"] == "success"                      # node-level
        assert body["node_id"] == "node_2"
        assert body["output"] == {"echoed": {"q": "hi"}}
    finally:
        current_sync_tenant_id.reset(tok)
    # node_execute stages the node job and submits it to the resident session.
    assert session.captured["staged"]["node"]["node_id"] == "node_2"
    assert session.captured["staged"]["inputs"] == {}
    assert session.captured["job"]["kind"] == "node"
    assert session.captured["job"]["run_subpath"] == "run"


@pytest.mark.asyncio
async def test_node_execute_inputs_override(app_engine, tmp_path, monkeypatch):
    import importlib; ne = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    wf = _wf(_node("ApiEchoTestNode", {"q": {"type": "string", "value": "hi", "reference": ""}}))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    session = _FakeSession(
        str(run_dir),
        str(tmp_path / "store"),
        final_outputs={"node_2": {"echoed": {"q": "override"}}},
    )
    rt = _Rt(PostgresVfsStore(), wf, session=session, tenant_id=str(t))
    tok = current_sync_tenant_id.set(str(t))
    try:
        body, art = _ca(await ne._node_execute("node_2", '{"q": "override"}', rt))
        assert body["output"] == {"echoed": {"q": "override"}}
    finally:
        current_sync_tenant_id.reset(tok)
    # The override inputs are parsed by node_execute and handed to the sandbox.
    assert session.captured["staged"]["inputs"] == {"q": "override"}


@pytest.mark.asyncio
async def test_node_execute_node_error_is_tool_ok_with_output_status_error(app_engine, tmp_path, monkeypatch):
    import importlib; ne = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    wf = _wf(_node("ApiBoomTestNode"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    session = _FakeSession(
        str(run_dir),
        str(tmp_path / "store"),
        error_dict={"node_2": "[ApiBoom]: kaboom"},
    )
    rt = _Rt(PostgresVfsStore(), wf, session=session, tenant_id=str(t))
    tok = current_sync_tenant_id.set(str(t))
    try:
        body, art = _ca(await ne._node_execute("node_2", "", rt))
        assert art["status"] == "success"            # tool ran fine
        assert body["status"] == "error"             # node failed
        assert "kaboom" in (body["error"] or "")
    finally:
        current_sync_tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_node_execute_unknown_node_is_tool_err(app_engine):
    from vibecanvas_api.services.platform_mcp.run_tools.node_execute import _node_execute
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    rt = _Rt(PostgresVfsStore(), _wf(_node("ApiEchoTestNode")))
    tok = current_sync_tenant_id.set(str(t))
    try:
        _body, art = _ca(await _node_execute("node_999", "", rt))
        assert art["status"] == "error" and art["error"]["code"] == "unknown_node"
    finally:
        current_sync_tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_node_execute_unknown_node_type_is_tool_err(app_engine):
    from vibecanvas_api.services.platform_mcp.run_tools.node_execute import _node_execute
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    rt = _Rt(PostgresVfsStore(), _wf(_node("NoSuchNodeType")))
    tok = current_sync_tenant_id.set(str(t))
    try:
        _body, art = _ca(await _node_execute("node_2", "", rt))
        assert art["status"] == "error" and art["error"]["code"] == "unknown_node_type"
    finally:
        current_sync_tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_node_execute_empty_inputs_does_not_crash(app_engine, tmp_path, monkeypatch):
    import importlib; ne = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    wf = _wf(_node("ApiEchoTestNode"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    session = _FakeSession(
        str(run_dir),
        str(tmp_path / "store"),
        final_outputs={"node_2": {"echoed": {}}},
    )
    rt = _Rt(PostgresVfsStore(), wf, session=session, tenant_id=str(t))
    tok = current_sync_tenant_id.set(str(t))
    try:
        _body, art = _ca(await ne._node_execute("node_2", "", rt))   # empty string default
        assert art["status"] == "success"
    finally:
        current_sync_tenant_id.reset(tok)
    assert session.captured["staged"]["inputs"] == {}  # empty string -> {} reaches the sandbox


@pytest.mark.asyncio
async def test_run_workflow_single_node_now_rejected_with_hint(app_engine):
    from vibecanvas_api.services.platform_mcp.run_tools.run_workflow import _sync_run_workflow
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    rt = _Rt(PostgresVfsStore(), _wf(_node("ApiEchoTestNode")))
    tok = current_sync_tenant_id.set(str(t))
    try:
        _body, art = _ca(await asyncio.to_thread(_sync_run_workflow, "node_2", "{}", rt))
        assert art["status"] == "error"
        assert art["error"]["code"] == "run_failed"
    finally:
        current_sync_tenant_id.reset(tok)


@pytest.mark.asyncio
async def test_parallel_node_execute_all_succeed(app_engine, tmp_path, monkeypatch):
    # The model may emit concurrent node_execute tool-calls. node_execute no
    # longer mints a distinct /logs file per call (it overwrites the one node
    # file in the workflow's latest run); the concurrency contract is simply
    # that every call runs to a successful node result without crashing.
    import importlib; ne = importlib.import_module("vibecanvas_api.services.platform_mcp.run_tools.node_execute")
    t, u = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, t, "wf_b", "chat_b", u)
    wf = _wf(_node("ApiEchoTestNode"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    session = _FakeSession(
        str(run_dir),
        str(tmp_path / "store"),
        final_outputs={"node_2": {"echoed": {}}},
    )
    rt = _Rt(PostgresVfsStore(), wf, session=session, tenant_id=str(t))
    tok = current_sync_tenant_id.set(str(t))
    try:
        outs = await asyncio.gather(*[ne._node_execute("node_2", "", rt) for _ in range(5)])
        parsed = [_ca(o) for o in outs]
        assert all(art["status"] == "success" for _body, art in parsed)
        assert all(body["status"] == "success" for body, _art in parsed)
    finally:
        current_sync_tenant_id.reset(tok)

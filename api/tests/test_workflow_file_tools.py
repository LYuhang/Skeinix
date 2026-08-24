from types import SimpleNamespace

import pytest


class _Session:
    def __init__(self):
        self.files = {}
        self.writebacks = 0

    async def write_file(self, path, content):
        self.files[path] = content
        await self.writeback_vfs()
        return {"ok": True, "bytes": len(content.encode())}

    async def read_file(self, path):
        if path not in self.files:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "kind": "text", "content": self.files[path]}

    async def writeback_vfs(self):
        self.writebacks += 1


class _Repo:
    def __init__(self):
        self.workflow = {
            "node_1": {"node_id": "node_1", "node_type": "StartNode", "children": []},
            "__meta__": {"workflow_id": "wf_1"},
        }
        self.commits = []
        self.saved = []

    def get_current_workflow(self, wf_id):
        return self.workflow

    def get_meta(self, wf_id):
        return {
            "wf_id": wf_id,
            "workflow_name": "Flow",
            "active_major": 1,
            "active_sub": 2,
        }

    def commit(self, wf_id, workflow, note=""):
        self.commits.append((wf_id, workflow, note))
        self.workflow = workflow
        return SimpleNamespace(sv=3)

    def mark_saved(self, wf_id):
        self.saved.append(wf_id)


def _runtime(session=None, repo=None):
    session = session or _Session()
    repo = repo or _Repo()

    async def sandbox_session():
        return session

    ctx = SimpleNamespace(
        current_workflow_id="wf_1",
        wf_id="__chatws_user_chat",
        repo=repo,
        workflow=repo.workflow,
        workflow_dirty=False,
        sandbox_session=sandbox_session,
    )
    return SimpleNamespace(context=ctx), session, repo


def test_auto_tidy_workflow_spreads_graph_left_to_right():
    from vibecanvas_api.services.platform_mcp.build_tools.workflow_file import _auto_tidy_workflow

    wf = {
        "node_1": {"node_id": "node_1", "node_type": "StartNode", "children": ["node_2", "node_3"], "__attributes__": {"x": 0, "y": 0}},
        "node_2": {"node_id": "node_2", "node_type": "CodeNode", "children": ["node_4"], "__attributes__": {"x": 0, "y": 0}},
        "node_3": {"node_id": "node_3", "node_type": "CodeNode", "children": ["node_4"], "__attributes__": {"x": 0, "y": 0}},
        "node_4": {"node_id": "node_4", "node_type": "EndNode", "children": [], "__attributes__": {"x": 0, "y": 0}},
    }

    _auto_tidy_workflow(wf)

    assert wf["node_1"]["__attributes__"]["x"] == 0
    assert wf["node_2"]["__attributes__"]["x"] == wf["node_3"]["__attributes__"]["x"]
    assert wf["node_2"]["__attributes__"]["x"] > wf["node_1"]["__attributes__"]["x"]
    assert wf["node_2"]["__attributes__"]["y"] != wf["node_3"]["__attributes__"]["y"]
    assert wf["node_4"]["__attributes__"]["x"] > wf["node_2"]["__attributes__"]["x"]


def test_auto_tidy_workflow_preserves_parallel_branch_lanes():
    """A short branch's long join edge must not cross a longer sibling node.

    This is the shape produced by a ParallelStart whose first branch contains
    a loop/code chain while its second branch goes directly to ParallelEnd.
    The old per-rank centring put node_9 and node_12 on y=0, so the visual edge
    node_12 -> node_18 appeared to terminate at node_9.
    """
    from vibecanvas_api.services.platform_mcp.build_tools.workflow_file import _auto_tidy_workflow

    def node(node_id, children):
        return {
            "node_id": node_id,
            "node_type": "CodeNode",
            "children": children,
            "__attributes__": {"x": 0, "y": 0},
        }

    wf = {
        "node_7": node("node_7", ["node_8", "node_12", "node_15"]),
        "node_8": node("node_8", ["node_9"]),
        "node_9": node("node_9", ["node_10"]),
        "node_10": node("node_10", ["node_11"]),
        "node_11": node("node_11", ["node_18"]),
        "node_12": node("node_12", ["node_18"]),
        "node_15": node("node_15", ["node_18"]),
        "node_18": node("node_18", []),
    }

    _auto_tidy_workflow(wf)

    y = lambda node_id: wf[node_id]["__attributes__"]["y"]
    x = lambda node_id: wf[node_id]["__attributes__"]["x"]

    # The long branch stays in node_8's lane instead of being re-centred onto
    # the short node_12 -> node_18 edge at each otherwise-singleton rank.
    assert y("node_9") == y("node_8")
    assert y("node_10") == y("node_8")
    assert y("node_11") == y("node_8")
    assert y("node_9") != y("node_12")

    # node_12 -> node_18 spans the ranks occupied by node_9/10/11.  Its two
    # endpoints share y=0 while those unrelated nodes are in another lane, so
    # the edge cannot visually masquerade as a parent edge into node_9.
    assert y("node_12") == y("node_18")
    assert x("node_12") < x("node_9") < x("node_18")


@pytest.mark.asyncio
async def test_get_workflow_exports_canvas_to_json_file(monkeypatch):
    import vibecanvas_api.services.platform_mcp.workflow_tools as workflow_tools

    rt, session, repo = _runtime()

    async def fake_load(_ctx, workflow_id, _action):
        return SimpleNamespace(
            workflow=repo.get_current_workflow(workflow_id),
            meta=repo.get_meta(workflow_id),
        )

    monkeypatch.setattr(
        workflow_tools,
        "load_authorized_workflow",
        fake_load,
    )
    content, artifact = await workflow_tools._do_get_workflow(
        rt,
        "/data/workflow.json",
    )

    assert "Exported current canvas workflow to /data/workflow.json" in content
    assert "Format: JSON object keyed by node ids" not in content
    assert artifact["status"] == "success"
    assert "/data/workflow.json" in session.files
    assert '"workflow_id": "wf_1"' in session.files["/data/workflow.json"]
    assert session.writebacks == 1


@pytest.mark.asyncio
async def test_get_workflow_accepts_explicit_id_without_chat_selection(monkeypatch):
    import vibecanvas_api.services.platform_mcp.workflow_tools as workflow_tools

    rt, session, repo = _runtime()
    rt.context.current_workflow_id = None

    async def fake_load(_ctx, workflow_id, _action):
        assert workflow_id == "wf_1"
        return SimpleNamespace(
            workflow=repo.get_current_workflow(workflow_id),
            meta=repo.get_meta(workflow_id),
        )

    monkeypatch.setattr(workflow_tools, "load_authorized_workflow", fake_load)
    content, artifact = await workflow_tools._do_get_workflow(
        rt,
        "/data/selected-workflow.json",
        workflow_id="wf_1",
    )

    assert "Workflow ID: wf_1" in content
    assert artifact["status"] == "success"
    assert "/data/selected-workflow.json" in session.files


@pytest.mark.asyncio
async def test_update_canvas_commits_valid_workflow_file(monkeypatch):
    import importlib
    mod = importlib.import_module("vibecanvas_api.services.platform_mcp.build_tools.update_canvas")

    rt, session, repo = _runtime()
    session.files["/data/workflow.json"] = (
        '{"node_1":{"node_id":"node_1","node_type":"StartNode","children":[]},'
        '"__meta__":{"workflow_id":"wf_1"}}'
    )
    async def valid(_workflow, _ctx):
        return []

    monkeypatch.setattr(mod, "validate_workflow_for_context", valid)

    content, artifact = await mod._do_update_canvas("/data/workflow.json", rt)

    assert "Canvas updated: yes" in content
    assert "Canvas updated from /data/workflow.json" in content
    assert artifact["status"] == "success"
    assert repo.commits
    assert repo.saved == ["wf_1"]


@pytest.mark.asyncio
async def test_update_canvas_blocks_invalid_file(monkeypatch):
    import importlib
    mod = importlib.import_module("vibecanvas_api.services.platform_mcp.build_tools.update_canvas")

    rt, session, repo = _runtime()
    session.files["/data/workflow.json"] = "{}"
    async def invalid(_workflow, _ctx):
        return [{"node_id": "global", "message": "missing StartNode"}]

    monkeypatch.setattr(mod, "validate_workflow_for_context", invalid)

    content, artifact = await mod._do_update_canvas("/data/workflow.json", rt)

    assert "Canvas updated: no" in content
    assert "Canvas was not updated" in content
    assert "Required next action" in content
    assert "Do not provide a final success answer" in content
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "validation_failed"
    assert repo.commits == []


@pytest.mark.asyncio
async def test_update_canvas_bad_json_guides_code_generated_json():
    import importlib
    mod = importlib.import_module("vibecanvas_api.services.platform_mcp.build_tools.update_canvas")

    rt, session, repo = _runtime()
    session.files["/data/workflow.json"] = "{'node_1': {'node_id': 'node_1'}}"

    content, artifact = await mod._do_update_canvas("/data/workflow.json", rt)

    assert "Canvas updated: no" in content
    assert "not valid JSON" in content
    assert "double quotes" in content
    assert "Python dict syntax with single quotes is not valid JSON" in content
    assert "json.dump(..., ensure_ascii=False, indent=2)" in content
    assert "python -m json.tool /data/workflow.json" in content
    assert "Required next action" in content
    assert "Do not provide a final success answer" in content
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "bad_json"
    assert repo.commits == []

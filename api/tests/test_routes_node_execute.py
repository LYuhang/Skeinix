# -*- coding: utf-8 -*-
"""Node-granularity debug-execution route.

``POST /api/v1/workflows/{wf_id}/nodes/{node_id}/execute`` runs ONE node —
the DRAFT node_dict carried in the request body (NOT the committed
snapshot) — and streams the synthesized ``running`` → ``completed`` /
``error`` frames via the SAME SSE machinery as the workflow route.

Uses the REAL auth harness (register → session_token → Bearer) on the
httpx ASGI ``client`` fixture (the legacy ``VIBECANVAS_API_DEV_TOKEN``
Bearer harness is dead).
"""
from __future__ import annotations

import json
import tempfile
import uuid
import base64

import pytest


@pytest.fixture
def _sandbox_fs(monkeypatch):
    """Resident sandbox jobs require a materializable filesystem object store.

    The shared ASGI client intentionally skips app lifespan and otherwise keeps
    the lightweight in-memory test store, which cannot provide the host `/run`
    mount used by node execution.
    """
    from vibecanvas_api.config import config as _cfg
    from vibecanvas_api.services.sandbox import _gvisor_runnable

    if not _gvisor_runnable():
        pytest.skip("full rootless gVisor profile is unavailable")
    monkeypatch.setattr(_cfg.object_store, "provider", "filesystem", raising=False)
    monkeypatch.setattr(
        _cfg.object_store,
        "fs_root",
        tempfile.mkdtemp(prefix="vc-node-os-"),
        raising=False,
    )
    monkeypatch.setattr(_cfg, "kms_provider", "local", raising=False)
    monkeypatch.setattr(
        _cfg,
        "kms_local_master_key",
        base64.urlsafe_b64encode(b"n" * 32).decode(),
        raising=False,
    )
    monkeypatch.setattr(_cfg, "kms_local_master_key_file", "", raising=False)


async def _register(client) -> str:
    email = f"ex2_{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": "Test User", "password": "pw12345678"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["session_token"]


async def _make_wf(client, hdr) -> str:
    r = await client.post("/api/v1/workflows", json={"name": "ex2"}, headers=hdr)
    assert r.status_code == 201, r.text
    return r.json()["wf_id"]


def _code_node(process_fn: str) -> dict:
    return {
        "node_id": "node_2", "node_name": "code", "node_type": "CodeNode",
        "node_description": "",
        "input_fields": {"x": {"type": "string", "value": "hi", "reference": ""}},
        "output_fields": {"y": {"type": "string", "description": ""}},
        "node_config": {
            "programming_language": "python",
            "process_fn": process_fn,
        },
        "children": [], "__attributes__": {"x": 0, "y": 0},
    }


async def _parse_sse(resp) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    buf = ""
    async for chunk in resp.aiter_bytes():
        buf += chunk.decode("utf-8")
        while "\n\n" in buf:
            block, _, buf = buf.partition("\n\n")
            name = data = None
            for line in block.split("\n"):
                if line.startswith("event: "):
                    name = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line.removeprefix("data: "))
                    except Exception:
                        data = {}
            if name is not None:
                events.append((name, data or {}))
    return events


@pytest.mark.asyncio
async def test_node_execute_streams_running_then_completed(client, pg_engine, _sandbox_fs):
    tok = await _register(client)
    hdr = {"Authorization": f"Bearer {tok}"}
    wf_id = await _make_wf(client, hdr)

    node = _code_node(
        "def process_fn(inputs):\n    return {'y': inputs['x'] + '!'}")
    async with client.stream(
        "POST", f"/api/v1/workflows/{wf_id}/nodes/node_2/execute",
        json={"node": node, "input": {"x": "world"}}, headers=hdr,
    ) as resp:
        assert resp.status_code == 200
        events = await _parse_sse(resp)

    names = [n for n, _ in events]
    assert names[0] == "started"
    assert names[-1] == "done", names[-3:]

    upd = [p for n, p in events if n == "EXEC_UPDATE"]
    statuses = [p["status"] for p in upd]
    assert "running" in statuses and "completed" in statuses
    assert statuses.index("running") < statuses.index("completed")
    completed = next(p for p in upd if p["status"] == "completed")
    assert completed["node_id"] == "node_2"
    assert json.loads(completed["result"]) == {"y": "world!"}
    # exec_id is stamped on every frame.
    assert all(p.get("exec_id") for p in upd)


@pytest.mark.asyncio
async def test_node_execute_uses_draft_body_not_committed(client, pg_engine, _sandbox_fs):
    """The node comes from the request body — there is NO committed
    workflow content at all, yet the run succeeds because the draft node
    is supplied directly (M2)."""
    tok = await _register(client)
    hdr = {"Authorization": f"Bearer {tok}"}
    wf_id = await _make_wf(client, hdr)
    # NOTE: we never commit any workflow content for this wf.

    node = _code_node("def process_fn(inputs):\n    return {'y': 'from-draft'}")
    async with client.stream(
        "POST", f"/api/v1/workflows/{wf_id}/nodes/node_2/execute",
        json={"node": node, "input": {}}, headers=hdr,
    ) as resp:
        events = await _parse_sse(resp)

    upd = [p for n, p in events if n == "EXEC_UPDATE"]
    completed = next(p for p in upd if p["status"] == "completed")
    assert json.loads(completed["result"]) == {"y": "from-draft"}


@pytest.mark.asyncio
async def test_node_execute_raising_node_streams_error(client, pg_engine, _sandbox_fs):
    tok = await _register(client)
    hdr = {"Authorization": f"Bearer {tok}"}
    wf_id = await _make_wf(client, hdr)

    node = _code_node("def process_fn(inputs):\n    raise ValueError('boom')")
    async with client.stream(
        "POST", f"/api/v1/workflows/{wf_id}/nodes/node_2/execute",
        json={"node": node, "input": {}}, headers=hdr,
    ) as resp:
        events = await _parse_sse(resp)

    names = [n for n, _ in events]
    assert names[-1] == "done", names[-3:]   # synthesized stream terminates cleanly
    upd = [p for n, p in events if n == "EXEC_UPDATE"]
    err = [p for p in upd if p["status"] == "error"]
    assert err, f"no error frame: {upd}"
    assert "boom" in err[0]["error"]


@pytest.mark.asyncio
async def test_node_execute_404_for_unknown_workflow(client, pg_engine):
    tok = await _register(client)
    hdr = {"Authorization": f"Bearer {tok}"}
    r = await client.post(
        "/api/v1/workflows/no_such_wf/nodes/node_2/execute",
        json={"node": _code_node("def process_fn(inputs):\n    return {}"),
              "input": {}},
        headers=hdr,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_node_execute_tenant_scoped(client, pg_engine):
    """A second tenant cannot debug-run a node against tenant A's wf — the
    wf-existence check is RLS-bound, so it 404s for the other tenant."""
    tok_a = await _register(client)
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    wf_id = await _make_wf(client, hdr_a)

    tok_b = await _register(client)
    hdr_b = {"Authorization": f"Bearer {tok_b}"}
    r = await client.post(
        f"/api/v1/workflows/{wf_id}/nodes/node_2/execute",
        json={"node": _code_node("def process_fn(inputs):\n    return {}"),
              "input": {}},
        headers=hdr_b,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_node_execute_requires_auth(client, pg_engine):
    r = await client.post(
        "/api/v1/workflows/wf_x/nodes/node_2/execute",
        json={"node": {}, "input": {}},
    )
    assert r.status_code == 401

"""CodeNode runs through the per-run CodeWorkerPool via the unified
thread bridge.

Contract:
  - A workflow with a CodeNode runs end-to-end; its output lands in
    previous_outputs (the worker-pool path is exercised by the engine run).
  - CodeNode no longer has a dedicated ``call_async`` dispatch branch — it flows
    through the unified ``REQUIRES_THREAD_BRIDGE`` → ``asyncio.to_thread`` path.
"""

from __future__ import annotations

import inspect

import pytest

from vibecanvas_engine.workflow import Workflow
from vibecanvas_engine.nodes.code import CodeNode


@pytest.mark.asyncio
async def test_codenode_runs_in_worker_pool(simple_codenode_workflow_dict, tmp_path):
    """A workflow with a CodeNode runs end-to-end; result lands in previous_outputs."""
    wf = Workflow(simple_codenode_workflow_dict)
    events = [
        ev
        async for ev in wf.astream({}, run_context={"run_id": "t", "run_dir": str(tmp_path)})
    ]
    finished = next(e for e in events if e.get("status") == "finished")
    # The fixture's CodeNode returns {"answer": 42}; assert that surfaces.
    assert any(
        v.get("answer") == 42
        for v in finished.get("final_outputs", {}).values()
        if isinstance(v, dict)
    ), f"Expected CodeNode to yield answer=42; got {finished.get('final_outputs')}"


def test_codenode_declares_thread_bridge():
    """CodeNode opts into the unified off-loop dispatch (no call_async branch)."""
    assert getattr(CodeNode, "REQUIRES_THREAD_BRIDGE", False) is True
    assert not hasattr(CodeNode, "call_async"), (
        "call_async must not remain on CodeNode"
    )


def test_dispatch_has_no_codenode_special_case():
    """The dispatcher routes CodeNode through the generic REQUIRES_THREAD_BRIDGE
    branch — no ``node_type == 'CodeNode'`` / ``call_async`` special-case."""
    from vibecanvas_engine.nodes import exec as exec_mod

    src = inspect.getsource(exec_mod)
    assert "call_async" not in src, (
        "nodes/exec.py still references call_async — CodeNode special-case not removed"
    )
    assert 'node.node_type == "CodeNode"' not in src, (
        "nodes/exec.py must not special-case CodeNode by node_type"
    )
    assert "REQUIRES_THREAD_BRIDGE" in src

"""RE-3 T1 — Workflow threads an optional ``run_context`` dict into the
``extra`` every node sees, fully backward-compatible when ``run_context`` is
``None``/absent.

The api layer builds ``run_context`` (run_id + run_dir, the real per-run
directory) and passes it into the pure engine; the engine merges it into
``extra`` without importing anything new.
"""
from __future__ import annotations

import pytest

from vibecanvas_engine import Workflow
from vibecanvas_engine.nodes.base import BaseNode
from vibecanvas_engine.register import node_registry
from vibecanvas_engine.utils import safe_call_with_args


# A capturing node that records the ``extra`` it receives so the test can
# assert on what the engine threaded down.
_CAPTURED_EXTRAS: list[dict] = []


class _CaptureExtraNode(BaseNode):
    NODE_TYPE = "CaptureExtraTestNode"
    # Take the thread-bridge dispatch path (exec.py) so the engine forwards
    # the real ``extra`` into ``__call__`` (the plain inline path drops it).
    REQUIRES_THREAD_BRIDGE = True

    @safe_call_with_args(prefix="[CaptureExtra]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        _CAPTURED_EXTRAS.append(extra)
        return {"x": inputs.get("x")}


node_registry._module_dict.setdefault("CaptureExtraTestNode", _CaptureExtraNode)


def _capture_workflow_dict() -> dict:
    """Start → CaptureExtraTestNode → End."""
    return {
        "__meta__": {
            "workflow_id": "wf_run_ctx",
            "workflow_name": "run_ctx_smoke",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {"x": {"type": "string", "value": "", "reference": ""}},
            "output_fields": {"x": {"type": "string", "description": "passthrough"}},
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "capture",
            "node_type": "CaptureExtraTestNode",
            "node_description": "capture extra",
            "input_fields": {
                "x": {"type": "string", "value": "", "reference": "__start__.x"},
            },
            "output_fields": {"x": {"type": "string", "description": "passthrough"}},
            "node_config": {},
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "x": {"type": "string", "value": "", "reference": "capture.x"},
            },
            "output_fields": {"x": {"type": "string", "description": "passthrough"}},
            "node_config": {},
            "children": [],
        },
    }


@pytest.fixture(autouse=True)
def _clear_captured():
    _CAPTURED_EXTRAS.clear()
    yield
    _CAPTURED_EXTRAS.clear()


@pytest.mark.asyncio
async def test_astream_threads_run_context_into_extra():
    rc = {"run_id": "r1", "run_dir": "/tmp/run/t/r1"}
    wf = Workflow(_capture_workflow_dict(), max_workers=4)

    events = []
    async for ev in wf.astream({"x": "hello"}, run_context=rc):
        events.append(ev)

    finished = next((e for e in events if e.get("status") == "finished"), None)
    assert finished is not None, f"no finished event: {[e.get('status') for e in events]}"
    assert not finished.get("error_dict"), f"errors: {finished['error_dict']}"

    assert _CAPTURED_EXTRAS, "capture node never ran"
    extra = _CAPTURED_EXTRAS[-1]
    assert extra["run_id"] == "r1"
    assert extra["run_dir"] == "/tmp/run/t/r1"


@pytest.mark.asyncio
async def test_astream_run_context_none_backward_compat():
    wf = Workflow(_capture_workflow_dict(), max_workers=4)

    events = []
    async for ev in wf.astream({"x": "hello"}):
        events.append(ev)

    finished = next((e for e in events if e.get("status") == "finished"), None)
    assert finished is not None, f"no finished event: {[e.get('status') for e in events]}"
    assert not finished.get("error_dict"), f"errors: {finished['error_dict']}"

    assert _CAPTURED_EXTRAS, "capture node never ran"
    extra = _CAPTURED_EXTRAS[-1]
    assert "run_id" not in extra
    assert "run_dir" not in extra

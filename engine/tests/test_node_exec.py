"""Engine-layer node-call dispatch and single-node runner."""
from __future__ import annotations

import threading

import pytest

from vibecanvas_engine.nodes.base import BaseNode
from vibecanvas_engine.register import node_registry
from vibecanvas_engine.utils import safe_call_with_args
from vibecanvas_engine.nodes.exec import dispatch_node_call


# --- stub nodes (registered idempotently so re-import never double-registers) ---
class _EchoNode(BaseNode):
    NODE_TYPE = "EchoTestNode"

    @safe_call_with_args(prefix="[EchoTest]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        return {"echoed": inputs}


class _ThreadEchoNode(BaseNode):
    NODE_TYPE = "ThreadEchoTestNode"
    REQUIRES_THREAD_BRIDGE = True

    @safe_call_with_args(prefix="[ThreadEchoTest]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        return {"thread_name": threading.current_thread().name, "echoed": inputs}


node_registry._module_dict.setdefault("EchoTestNode", _EchoNode)
node_registry._module_dict.setdefault("ThreadEchoTestNode", _ThreadEchoNode)


def _node(node_type: str, **over) -> BaseNode:
    cls = node_registry._module_dict[node_type]
    nd = {"node_type": node_type, "node_id": "node_1", "node_name": "n",
          "node_description": "", "input_fields": {}, "output_fields": {},
          "node_config": {}, "children": []}
    nd.update(over)
    return cls(**nd)


@pytest.mark.asyncio
async def test_dispatch_plain_sync_node_calls_dunder_call():
    res = await dispatch_node_call(_node("EchoTestNode"), {"a": 1}, {}, {})
    assert res["status"] == "success"
    assert res["output"] == {"echoed": {"a": 1}}


@pytest.mark.asyncio
async def test_dispatch_thread_bridge_node_runs_off_main_thread():
    res = await dispatch_node_call(_node("ThreadEchoTestNode"), {"a": 1}, {}, {})
    assert res["status"] == "success"
    # asyncio.to_thread ran it on a worker thread, not the main/event-loop thread.
    assert res["output"]["thread_name"] != threading.main_thread().name


from vibecanvas_engine.nodes.exec import run_node, UnknownNodeType


class _BoomNode(BaseNode):
    NODE_TYPE = "BoomTestNode"

    @safe_call_with_args(prefix="[BoomTest]: ")
    def __call__(self, inputs, previous_outputs, extra=None):
        raise ValueError("boom")


node_registry._module_dict.setdefault("BoomTestNode", _BoomNode)


def _node_dict(node_type: str, input_fields: dict | None = None) -> dict:
    return {"node_type": node_type, "node_id": "node_1", "node_name": "n",
            "node_description": "", "input_fields": input_fields or {},
            "output_fields": {}, "node_config": {}, "children": []}


@pytest.mark.asyncio
async def test_run_node_seeds_inputs_from_field_values():
    nd = _node_dict("EchoTestNode", {"q": {"type": "string", "value": "hello", "reference": ""}})
    res = await run_node(nd)
    assert res["status"] == "success"
    assert res["output"] == {"echoed": {"q": "hello"}}


@pytest.mark.asyncio
async def test_run_node_caller_inputs_override_field_values():
    nd = _node_dict("EchoTestNode", {"q": {"type": "string", "value": "hello", "reference": ""}})
    res = await run_node(nd, {"q": "override"})
    assert res["output"] == {"echoed": {"q": "override"}}


@pytest.mark.asyncio
async def test_run_node_does_not_resolve_references():
    # reference-only field (no meaningful value) is NOT resolved → value passes as-is (None).
    nd = _node_dict("EchoTestNode", {"q": {"type": "string", "value": None, "reference": "other.out"}})
    res = await run_node(nd)
    assert res["output"] == {"echoed": {"q": None}}


@pytest.mark.asyncio
async def test_run_node_unknown_node_type_raises():
    with pytest.raises(UnknownNodeType):
        await run_node(_node_dict("NoSuchNodeType"))


@pytest.mark.asyncio
async def test_run_node_node_error_returns_status_error_not_raised():
    res = await run_node(_node_dict("BoomTestNode"))
    assert res["status"] == "error"
    assert "boom" in res["error_message"]

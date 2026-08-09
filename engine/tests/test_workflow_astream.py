"""Workflow.astream yields events as an async iterator;
_execute and all Node.trigger methods are async.

Contract:
  - Workflow.astream is an async generator function (`async def` + `yield`).
  - Workflow._execute is a coroutine function (`async def`).
  - BaseNode.trigger (and therefore every subclass) is a coroutine function.

We deliberately do NOT assert on the legacy sync `Workflow.trigger`
contract here — that's preserved as a thin asyncio.run() adapter and will
be polished further in T8.
"""

from __future__ import annotations

import inspect

import pytest


def test_astream_is_async_generator_function():
    from vibecanvas_engine.workflow import Workflow

    assert inspect.isasyncgenfunction(Workflow.astream), (
        "Workflow.astream must be `async def` with `yield`"
    )


def test_execute_is_async():
    from vibecanvas_engine.workflow import Workflow

    assert inspect.iscoroutinefunction(Workflow._execute), (
        "Workflow._execute must be `async def`"
    )


def test_all_node_trigger_methods_are_async():
    """Every BaseNode subclass's trigger() must be a coroutine function.

    The engine attaches a single ``trigger`` implementation to ``BaseNode``
    via ``nodes/__init__.py``; subclasses inherit it. So this test exercises
    both: BaseNode itself and every concrete subclass.
    """
    import pkgutil

    from vibecanvas_engine import node as node_mod  # noqa: F401  (forces shim load)
    from vibecanvas_engine.node import BaseNode
    from vibecanvas_engine import nodes as nodes_pkg

    # Collect node classes from the nodes/ subpackage modules.
    subclasses: list[type] = []
    sources = [node_mod, nodes_pkg]
    for info in pkgutil.iter_modules(nodes_pkg.__path__):
        full = f"{nodes_pkg.__name__}.{info.name}"
        sources.append(__import__(full, fromlist=["*"]))

    for mod in sources:
        for n in dir(mod):
            obj = getattr(mod, n)
            if isinstance(obj, type) and issubclass(obj, BaseNode) and obj is not BaseNode:
                subclasses.append(obj)

    subclasses = list({c.__name__: c for c in subclasses}.values())
    assert len(subclasses) >= 8, (
        f"Expected ~15 node classes; found {len(subclasses)}: "
        f"{sorted(c.__name__ for c in subclasses)}"
    )

    # BaseNode itself.
    assert inspect.iscoroutinefunction(BaseNode.trigger), (
        f"BaseNode.trigger must be `async def` (got {BaseNode.trigger!r})"
    )
    # Every concrete subclass.
    for cls in subclasses:
        assert inspect.iscoroutinefunction(cls.trigger), (
            f"{cls.__name__}.trigger must be `async def` (got {cls.trigger!r})"
        )


@pytest.mark.asyncio
async def test_astream_yields_finished_event_for_minimal_workflow():
    """End-to-end: astream over the canonical example workflow must emit a
    'finished' event with the same outputs the legacy sync trigger produces.
    """
    import json
    from pathlib import Path

    from vibecanvas_engine import Workflow

    example_path = Path(__file__).resolve().parent / "example_workflow.json"
    wf_dict = json.loads(example_path.read_text())
    wf = Workflow(wf_dict, max_workers=4)

    events = []
    async for ev in wf.astream({"text": "hi", "count": 2}):
        events.append(ev)

    finished = next((e for e in events if e.get("status") == "finished"), None)
    assert finished is not None, f"no finished event in stream: {events}"
    assert finished["final_outputs"].get("__end__") == {
        "repeated": "hi hi",
        "char_count": 5,
    }, f"unexpected outputs: {finished['final_outputs']}"
    assert not finished.get("error_dict"), f"errors: {finished['error_dict']}"


@pytest.mark.asyncio
async def test_node_error_event_preserves_resolved_inputs_for_debug_replay():
    """A failed workflow node must still expose the exact inputs it received.

    The API persists this event under ``__exec__/nodes`` and the Run node panel
    uses it as the reproduction payload.
    """
    import json
    from pathlib import Path

    from vibecanvas_engine import Workflow

    wf_dict = json.loads(
        (Path(__file__).resolve().parent / "example_workflow.json").read_text()
    )
    wf_dict["node_2"]["node_config"]["process_fn"] = (
        "def process_fn(inputs):\n    raise ValueError('expected failure')"
    )
    events = []
    async for ev in Workflow(wf_dict, max_workers=4).astream(
        {"text": "debug me", "count": 3}
    ):
        events.append(ev)

    failed = next(
        ev for ev in events
        if ev.get("node_id") == "node_2" and ev.get("status") == "error"
    )
    assert failed["inputs"] == {"text": "debug me", "count": 3}


def _parallel_workflow_dict() -> dict:
    """Build a small Start → ParallelStart → (A, B) → ParallelEnd → End
    workflow so the asyncio.create_task spawn path is exercised."""
    return {
        "__meta__": {
            "workflow_id": "wf_parallel",
            "workflow_name": "parallel_smoke",
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
            "node_name": "psplit",
            "node_type": "ParallelStartNode",
            "node_description": "split",
            "input_fields": {},
            "output_fields": {},
            "node_config": {
                "branches": {
                    "a": {"branch_description": "branch a", "next_node_id": "node_3"},
                    "b": {"branch_description": "branch b", "next_node_id": "node_4"},
                },
                "parallel_end_node_id": "node_5",
            },
            "children": ["node_3", "node_4"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "branch_a",
            "node_type": "CodeNode",
            "node_description": "a",
            "input_fields": {
                "x": {"type": "string", "value": "", "reference": "__start__.x"},
            },
            "output_fields": {"y": {"type": "string", "description": "a"}},
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'y': inputs['x'] + '-A'}",
            },
            "children": ["node_5"],
        },
        "node_4": {
            "node_id": "node_4",
            "node_name": "branch_b",
            "node_type": "CodeNode",
            "node_description": "b",
            "input_fields": {
                "x": {"type": "string", "value": "", "reference": "__start__.x"},
            },
            "output_fields": {"y": {"type": "string", "description": "b"}},
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'y': inputs['x'] + '-B'}",
            },
            "children": ["node_5"],
        },
        "node_5": {
            "node_id": "node_5",
            "node_name": "pmerge",
            "node_type": "ParallelEndNode",
            "node_description": "merge",
            "input_fields": {},
            "output_fields": {},
            "node_config": {"parallel_start_node_id": "node_2"},
            "children": ["node_6"],
        },
        "node_6": {
            "node_id": "node_6",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "a": {"type": "string", "value": "", "reference": "branch_a.y"},
                "b": {"type": "string", "value": "", "reference": "branch_b.y"},
            },
            "output_fields": {
                "a": {"type": "string", "description": "a"},
                "b": {"type": "string", "description": "b"},
            },
            "node_config": {},
            "children": [],
        },
    }


@pytest.mark.asyncio
async def test_astream_handles_parallel_branches():
    """Exercise the asyncio.create_task spawn path for ParallelStartNode.

    Both branches must complete before the ParallelEndNode fires and the
    workflow finishes — proving the pending-counter + done_event drain is
    intact under the new coroutine-based concurrency model.
    """
    from vibecanvas_engine import Workflow

    wf = Workflow(_parallel_workflow_dict(), max_workers=4)

    events = []
    async for ev in wf.astream({"x": "hello"}):
        events.append(ev)

    finished = next((e for e in events if e.get("status") == "finished"), None)
    assert finished is not None, f"no finished event: {[e.get('status') for e in events]}"
    assert not finished.get("error_dict"), f"errors: {finished['error_dict']}"

    end_scope = finished["final_outputs"].get("__end__")
    assert end_scope == {"a": "hello-A", "b": "hello-B"}, (
        f"unexpected end scope: {end_scope}"
    )

# -*- coding: utf-8 -*-
"""
Regression: a loop-body node reading `<begin>.loop_output[-1]` must see the
PREVIOUS completed iteration, never the in-progress one.

Bug (pre-fix): trigger.py appended an empty `{}` slot to `loop_output` BEFORE
entering the body, so a body node that read `loop_output[-1]["<self>"]` hit the
current (still-empty) slot and raised KeyError on every iteration after the
first. The contract (LoopBeginNode.AGENT_SPEC) is that loop_output appends "at
each END of the iteration step" — so the in-progress iteration must NOT be
visible to the body. The fix carries the iteration's scratch on the loop_stack
frame and commits it to loop_output only at LoopEnd.
"""

import pytest

from vibecanvas_engine.workflow import Workflow


_ACCUMULATE_FN = (
    "def process_fn(inputs):\n"
    "    if inputs['i'] == 0:\n"
    "        return {'accumulate_double': inputs['start_value'] * 2}\n"
    "    else:\n"
    "        prev = inputs['loop_output'][-1]['code3']['accumulate_double']\n"
    "        return {'accumulate_double': prev * 2}\n"
)


def _accumulate_loop_wf() -> dict:
    """Start → LoopBegin(lb, 0..3) → code3 → LoopEnd → End.

    code3 reads its OWN previous-iteration output via `lb.loop_output[-1].code3`
    — the exact pattern a user hit. start_value=1, step=1, end=3:
        i=0 → 1*2 = 2
        i=1 → loop_output[-1] (= iter0) = 2 → 4
        i=2 → loop_output[-1] (= iter1) = 4 → 8
    """
    return {
        "__meta__": {
            "workflow_id": "wf_loop_acc",
            "workflow_name": "loop_accumulate",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "",
            "input_fields": {},
            "output_fields": {},
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "lb",
            "node_type": "LoopBeginNode",
            "node_description": "",
            "input_fields": {},
            "output_fields": {
                "loop_output": {"type": "array", "description": ""},
                "i": {"type": "integer", "description": ""},
            },
            "node_config": {
                "init_value": {"value": 0, "reference": ""},
                "end_value": {"value": 3, "reference": ""},
                "step_value": 1,
                "loop_end_node_id": "node_4",
            },
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "code3",
            "node_type": "CodeNode",
            "node_description": "",
            "input_fields": {
                "i": {"type": "integer", "value": 0, "reference": "lb.i"},
                "loop_output": {
                    "type": "array",
                    "value": [],
                    "reference": "lb.loop_output",
                },
                "start_value": {"type": "integer", "value": 1, "reference": ""},
            },
            "output_fields": {
                "accumulate_double": {"type": "integer", "description": ""}
            },
            "node_config": {
                "programming_language": "python",
                "process_fn": _ACCUMULATE_FN,
            },
            "children": ["node_4"],
        },
        "node_4": {
            "node_id": "node_4",
            "node_name": "le",
            "node_type": "LoopEndNode",
            "node_description": "",
            "input_fields": {},
            "output_fields": {},
            "node_config": {"loop_begin_node_id": "node_2"},
            "children": ["node_5"],
        },
        "node_5": {
            "node_id": "node_5",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "",
            "input_fields": {},
            "output_fields": {},
            "node_config": {},
            "children": [],
        },
    }


@pytest.mark.asyncio
async def test_body_reads_previous_iteration_via_loop_output_minus_one():
    """The body's `loop_output[-1]` resolves to the prior FINISHED iteration —
    no KeyError, and the doubling accumulates 2 → 4 → 8."""
    wf = Workflow(_accumulate_loop_wf(), max_workers=4)

    events = []
    async for ev in wf.astream({}):
        events.append(ev)

    finished = next((e for e in events if e.get("status") == "finished"), None)
    assert finished is not None, f"no finished event: {[e.get('status') for e in events]}"
    # The pre-fix bug surfaced here as a CodeNode input/exec KeyError.
    assert not finished.get("error_dict"), f"unexpected errors: {finished['error_dict']}"

    # Each iteration's code3 success frame carries its output. Collect them in
    # order: the doubling chain must be exactly [2, 4, 8].
    code3_outputs = [
        e["output"]["accumulate_double"]
        for e in events
        if e.get("node_id") == "node_3"
        and e.get("status") == "success"
        and isinstance(e.get("output"), dict)
        and "accumulate_double" in e["output"]
    ]
    assert code3_outputs == [2, 4, 8], f"accumulation chain wrong: {code3_outputs}"


@pytest.mark.asyncio
async def test_final_loop_output_holds_all_completed_iterations():
    """After the loop, lb.loop_output holds exactly the 3 committed iterations,
    each keyed by the body node name — and NO trailing empty in-progress slot."""
    wf = Workflow(_accumulate_loop_wf(), max_workers=4)

    finished = None
    async for ev in wf.astream({}):
        if ev.get("status") == "finished":
            finished = ev

    assert finished is not None and not finished.get("error_dict")

    outputs = finished.get("final_outputs") or {}
    lb = outputs.get("lb")
    assert isinstance(lb, dict), f"no lb output in finished payload: {list(outputs)}"
    loop_output = lb.get("loop_output")
    assert isinstance(loop_output, list), loop_output
    # Exactly 3 completed iterations — no extra empty slot.
    assert len(loop_output) == 3, f"expected 3 iterations, got {loop_output}"
    assert [it["code3"]["accumulate_double"] for it in loop_output] == [2, 4, 8]


def _nested_loop_wf() -> dict:
    """Start → LB outer(0..2) → LB inner(0..2) → code → LE inner → LE outer → End.

    The inner body writes `outer.i * 10 + inner.i`. This exercises the nested
    scope chain: the inner loop's loop_output must live INSIDE the outer
    iteration's scratch, and BOTH levels must commit at their respective
    LoopEnd (no in-progress slots leaking).
    """
    inner_fn = (
        "def process_fn(inputs):\n"
        "    return {'v': inputs['oi'] * 10 + inputs['ii']}\n"
    )
    return {
        "__meta__": {
            "workflow_id": "wf_nested",
            "workflow_name": "nested_loop",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1", "node_name": "__start__", "node_type": "StartNode",
            "node_description": "", "input_fields": {}, "output_fields": {},
            "node_config": {}, "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2", "node_name": "outer", "node_type": "LoopBeginNode",
            "node_description": "",
            "input_fields": {},
            "output_fields": {
                "loop_output": {"type": "array", "description": ""},
                "i": {"type": "integer", "description": ""},
            },
            "node_config": {
                "init_value": {"value": 0, "reference": ""},
                "end_value": {"value": 2, "reference": ""},
                "step_value": 1, "loop_end_node_id": "node_6",
            },
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3", "node_name": "inner", "node_type": "LoopBeginNode",
            "node_description": "",
            "input_fields": {},
            "output_fields": {
                "loop_output": {"type": "array", "description": ""},
                "i": {"type": "integer", "description": ""},
            },
            "node_config": {
                "init_value": {"value": 0, "reference": ""},
                "end_value": {"value": 2, "reference": ""},
                "step_value": 1, "loop_end_node_id": "node_5",
            },
            "children": ["node_4"],
        },
        "node_4": {
            "node_id": "node_4", "node_name": "code", "node_type": "CodeNode",
            "node_description": "",
            "input_fields": {
                "oi": {"type": "integer", "value": 0, "reference": "outer.i"},
                "ii": {"type": "integer", "value": 0, "reference": "inner.i"},
            },
            "output_fields": {"v": {"type": "integer", "description": ""}},
            "node_config": {
                "programming_language": "python", "process_fn": inner_fn,
            },
            "children": ["node_5"],
        },
        "node_5": {
            "node_id": "node_5", "node_name": "le_inner", "node_type": "LoopEndNode",
            "node_description": "", "input_fields": {}, "output_fields": {},
            "node_config": {"loop_begin_node_id": "node_3"}, "children": ["node_6"],
        },
        "node_6": {
            "node_id": "node_6", "node_name": "le_outer", "node_type": "LoopEndNode",
            "node_description": "", "input_fields": {}, "output_fields": {},
            "node_config": {"loop_begin_node_id": "node_2"}, "children": ["node_7"],
        },
        "node_7": {
            "node_id": "node_7", "node_name": "__end__", "node_type": "EndNode",
            "node_description": "", "input_fields": {}, "output_fields": {},
            "node_config": {}, "children": [],
        },
    }


@pytest.mark.asyncio
async def test_nested_loops_commit_each_level_at_its_end():
    """Nested loops: outer.loop_output has 2 outer iterations; each holds the
    inner loop's 2 committed iterations under `inner.loop_output`, values
    `oi*10 + ii` — no empty in-progress slots at either level."""
    wf = Workflow(_nested_loop_wf(), max_workers=4)

    finished = None
    async for ev in wf.astream({}):
        if ev.get("status") == "finished":
            finished = ev

    assert finished is not None and not finished.get("error_dict"), finished

    outer = finished["final_outputs"]["outer"]
    assert len(outer["loop_output"]) == 2, outer["loop_output"]
    grid = [
        [it["code"]["v"] for it in oit["inner"]["loop_output"]]
        for oit in outer["loop_output"]
    ]
    assert grid == [[0, 1], [10, 11]], grid

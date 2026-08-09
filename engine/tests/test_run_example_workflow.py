"""Gate 2 — behavior parity: vibecanvas_engine.Workflow.trigger produces
the same output as legacy gradio_vibecanvas.core.Workflow.trigger for
the canonical example workflow.

example_workflow.json is the same file the legacy offline runner
uses, so the legacy 'known good' output is what /offline/run_workflow.py
prints when MODE='single'.
"""

from __future__ import annotations

import json
from pathlib import Path

from vibecanvas_engine import Workflow


def test_example_workflow_trigger_matches_legacy_known_good(example_workflow_path: Path):
    """Loads example_workflow.json, runs the new engine, asserts the
    output matches the legacy known-good values."""
    wf_dict = json.loads(example_workflow_path.read_text())
    wf = Workflow(wf_dict, max_workers=4)

    inputs = {"text": "hello", "count": 3}
    previous_outputs, errors, duration = wf.trigger(inputs)

    assert not errors, f"engine reported errors: {errors}"
    assert duration >= 0

    end_outputs = previous_outputs.get("__end__")
    assert end_outputs is not None, f"no __end__ scope in outputs: {previous_outputs}"
    assert end_outputs == {
        "repeated": "hello hello hello",
        "char_count": 17,
    }, f"output mismatch: {end_outputs}"


def test_workflow_check_accepts_example(example_workflow_path: Path):
    """Sanity: the example also passes the static graph validation."""
    wf_dict = json.loads(example_workflow_path.read_text())
    Workflow.check(wf_dict)


def test_trigger_with_different_inputs_produces_different_output(example_workflow_path: Path):
    """Spot-check the engine actually consumes the inputs (not memoized)."""
    wf_dict = json.loads(example_workflow_path.read_text())
    wf = Workflow(wf_dict, max_workers=4)

    out_a, _, _ = wf.trigger({"text": "hi", "count": 2})
    out_b, _, _ = wf.trigger({"text": "yo", "count": 4})

    assert out_a["__end__"]["repeated"] != out_b["__end__"]["repeated"]
    assert out_a["__end__"]["char_count"] != out_b["__end__"]["char_count"]

"""Shared Engine integration-test fixtures.

Tests run against the installed vibecanvas_engine package, NOT against
the source tree. Run `pip install -e .` from `engine/` first.
"""

from __future__ import annotations

from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).resolve().parent
EXAMPLE_WORKFLOW_PATH = TESTS_DIR / "example_workflow.json"

# Path to the legacy repo for cross-checks (e.g. Gate 5).
LEGACY_REPO = TESTS_DIR.parents[2] / "vibecanvas"


@pytest.fixture
def example_workflow_path() -> Path:
    """Filesystem path to the canonical smoke workflow."""
    return EXAMPLE_WORKFLOW_PATH


@pytest.fixture
def simple_codenode_workflow_dict() -> dict:
    """Minimal Start → CodeNode(returns {"answer": 42}) → End workflow.

    Used to exercise the subprocess-backed CodeNode path.
    """
    return {
        "__meta__": {
            "workflow_id": "wf_codenode_t7",
            "workflow_name": "codenode_smoke",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {},
            "output_fields": {},
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "compute",
            "node_type": "CodeNode",
            "node_description": "return the answer",
            "input_fields": {},
            "output_fields": {
                "answer": {"type": "integer", "description": "the answer"},
            },
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'answer': 42}",
            },
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "answer": {"type": "integer", "value": 0, "reference": "compute.answer"},
            },
            "output_fields": {
                "answer": {"type": "integer", "description": "the answer"},
            },
            "node_config": {},
            "children": [],
        },
    }


@pytest.fixture
def simple_workflow_dict() -> dict:
    """Minimal Start-to-End workflow with no compute.

    Exercises the sync ``Workflow.trigger`` legacy wrapper without pulling in
    any sandbox / LLM dependencies — Start emits an output, End references it.
    """
    return {
        "__meta__": {
            "workflow_id": "wf_t8_simple",
            "workflow_name": "simple_start_end",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {},
            "output_fields": {
                "x": {"type": "string", "description": "passthrough"},
            },
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "x": {"type": "string", "value": "", "reference": "__start__.x"},
            },
            "output_fields": {
                "x": {"type": "string", "description": "passthrough"},
            },
            "node_config": {},
            "children": [],
        },
    }

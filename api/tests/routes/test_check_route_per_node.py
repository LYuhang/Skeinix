"""Route ``POST /workflows/{wf_id}/check`` — per-node check wiring.

Stream-Engine: ``Workflow.check`` now invokes each node class's per-node
``check()`` after the structural pass, so the Check button is authoritative
("Check ✓" ⟺ runnable). These tests drive the ``check_workflow`` route
content helper directly with a lightweight async repo stub (the helper reads
``get_meta`` + ``get_current_workflow``), proving the new per-node failures
surface in ``CheckResponse.error_message`` — without the auth/DB harness.

The companion engine-level coverage (valid + each per-node failure +
no-regression) lives in ``engine/tests/test_workflow_check.py``.
"""
from __future__ import annotations

import copy

import pytest

from vibecanvas_api.routes.workflows import _check_workflow_content
from vibecanvas_api.schemas.workflow import CheckResponse


class _StubRepo:
    """Minimal async stand-in for ``WorkflowRepo``. ``check_workflow`` only
    awaits ``get_meta`` (existence gate) and ``get_current_workflow``."""

    def __init__(self, wf: dict | None):
        self._wf = wf

    async def get_meta(self, wf_id: str):
        return {"wf_id": wf_id} if self._wf is not None else None

    async def get_current_workflow(self, wf_id: str):
        return self._wf


def _condition_workflow() -> dict:
    """Start → Condition → (Code_a | Code_b). Structurally valid AND
    per-node valid: both children mapped + a mandatory "others" fallback."""
    return {
        "__meta__": {"workflow_id": "wf_cond", "workflow_version": 1,
                     "workflow_subversion": 0},
        "node_1": {
            "node_id": "node_1", "node_name": "__start__",
            "node_type": "StartNode", "node_description": "start",
            "input_fields": {"n": {"type": "integer", "value": 0, "reference": ""}},
            "output_fields": {"n": {"type": "integer", "description": "n"}},
            # StartNode.CONFIG_SCHEMA requires node_config == {} (additionalProperties
            # False); the legacy fixture's stale "process_fn" key now fails Check.
            "node_config": {}, "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2", "node_name": "router",
            "node_type": "ConditionNode", "node_description": "route",
            "input_fields": {"n": {"type": "integer", "value": 0, "reference": "__start__.n"}},
            "output_fields": {"condition": {"type": "string", "description": "branch"}},
            "node_config": {"conditions": [
                {"condition_name": "pos", "condition_str": "{n} > 0", "next_node_id": "node_3"},
                {"condition_name": "others", "condition_str": "others", "next_node_id": "node_4"},
            ]},
            "children": ["node_3", "node_4"],
        },
        "node_3": {
            "node_id": "node_3", "node_name": "branch_a",
            "node_type": "CodeNode", "node_description": "a",
            "input_fields": {}, "output_fields": {"r": {"type": "integer", "description": "r"}},
            "node_config": {"programming_language": "python",
                            "process_fn": "def process_fn(inputs):\n    return {'r': 1}"},
            "children": [],
        },
        "node_4": {
            "node_id": "node_4", "node_name": "branch_b",
            "node_type": "CodeNode", "node_description": "b",
            "input_fields": {}, "output_fields": {"r": {"type": "integer", "description": "r"}},
            "node_config": {"programming_language": "python",
                            "process_fn": "def process_fn(inputs):\n    return {'r': 2}"},
            "children": [],
        },
    }


@pytest.mark.asyncio
async def test_check_route_accepts_valid_workflow():
    resp: CheckResponse = await _check_workflow_content(
        "wf_cond",
        body=None,
        repo=_StubRepo(_condition_workflow()),
    )
    assert resp.status == "success"
    assert not resp.error_message


@pytest.mark.asyncio
async def test_check_route_reports_condition_conditions_not_equal_children():
    """A ConditionNode whose mapped next_node_ids ≠ children — structurally
    valid but per-node invalid — is now reported by the route Check."""
    wf = _condition_workflow()
    # Re-point the "others" fallback off node_4 → node_4 is an unmapped child.
    wf["node_2"]["node_config"]["conditions"][1]["next_node_id"] = "node_3"
    resp: CheckResponse = await _check_workflow_content(
        "wf_cond",
        body=None,
        repo=_StubRepo(wf),
    )
    assert resp.status == "error"
    assert "node_2" in resp.error_message
    assert "ConditionNode" in resp.error_message


@pytest.mark.asyncio
async def test_check_route_preserves_structural_error_message():
    """A structural failure (two StartNodes) still yields the original
    structural message — the per-node pass is additive, not a regression."""
    wf = _condition_workflow()
    wf["node_5"] = copy.deepcopy(wf["node_1"])
    wf["node_5"]["node_id"] = "node_5"
    resp: CheckResponse = await _check_workflow_content(
        "wf_cond",
        body=None,
        repo=_StubRepo(wf),
    )
    assert resp.status == "error"
    assert "StartNode" in resp.error_message


@pytest.mark.asyncio
async def test_check_route_rejects_unavailable_prompt_model():
    wf = _condition_workflow()
    wf["node_2"] = {
        "node_id": "node_2",
        "node_name": "prompt",
        "node_type": "PromptNode",
        "node_description": "prompt",
        "input_fields": {
            "n": {"type": "integer", "value": 0, "reference": "__start__.n"},
        },
        "output_fields": {
            "answer": {"type": "string", "description": "answer"},
        },
        "node_config": {
            "prompt_template": "# Task\nUse {{n}}.\n# Output Format\n{\"answer\": \"text\"}",
            "model_name": "gpt-4o-mini",
            "inference_config": {
                "temperature": 0.3,
                "max_tokens": 128,
                "top_k": -1,
                "top_p": 0.9,
            },
        },
        "children": ["node_3"],
    }
    # Keep the graph single-parent / reachable after replacing the condition.
    wf.pop("node_4")
    resp: CheckResponse = await _check_workflow_content(
        "wf_cond",
        body=None,
        repo=_StubRepo(wf),
        available_model_ids={"FreeKey"},
    )
    assert resp.status == "error"
    assert "gpt-4o-mini" in (resp.error_message or "")
    assert "FreeKey" in (resp.error_message or "")

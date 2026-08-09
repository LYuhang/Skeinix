"""Spot-check Workflow.check rejects malformed graphs.

Not exhaustive — the full per-invariant coverage lives in the
The legacy protocol-conformance suite included these sanity
checks that the port preserved the validator behavior.

Workflow.check returns {"status": "success"} or {"status": "error",
"error_message": "..."} — it does NOT raise on validation failure.
"""

from __future__ import annotations

import copy

from vibecanvas_engine import Workflow


def _minimal_valid_workflow() -> dict:
    """A bare 2-node Start → End workflow that should pass Workflow.check."""
    return {
        "__meta__": {
            "workflow_id": "wf_test",
            "workflow_name": "minimal",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {
                "x": {"type": "string", "value": "", "reference": ""},
            },
            "output_fields": {
                "x": {"type": "string", "description": "passthrough"},
            },
            "node_config": {},
            "children": ["node_2"],
            "__attributes__": {"x": 0, "y": 0},
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
            "__attributes__": {"x": 200, "y": 0},
        },
    }


def test_check_accepts_minimal_valid_workflow():
    result = Workflow.check(_minimal_valid_workflow())
    assert result == {"status": "success"}, f"unexpected result: {result}"


def test_check_rejects_workflow_with_zero_start_nodes():
    wf = _minimal_valid_workflow()
    wf["node_1"]["node_type"] = "CodeNode"
    wf["node_1"]["node_name"] = "not_start"
    wf["node_1"]["node_config"] = {
        "programming_language": "python",
        "process_fn": "def process_fn(inputs): return {'x': inputs['x']}",
    }
    result = Workflow.check(wf)
    assert result["status"] == "error"
    assert "StartNode" in result["error_message"]


def test_check_rejects_workflow_with_two_start_nodes():
    wf = _minimal_valid_workflow()
    wf["node_3"] = copy.deepcopy(wf["node_1"])
    wf["node_3"]["node_id"] = "node_3"
    result = Workflow.check(wf)
    assert result["status"] == "error"


def test_check_rejects_invalid_reference():
    wf = _minimal_valid_workflow()
    wf["node_2"]["input_fields"]["x"]["reference"] = "ghost_node.x"
    result = Workflow.check(wf)
    assert result["status"] == "error"
    assert "invalid node name" in result["error_message"]


def test_check_rejects_reference_missing_output_field_name():
    wf = _minimal_valid_workflow()
    wf["node_2"]["input_fields"]["x"]["reference"] = "__start__"

    result = Workflow.check(wf)

    assert result["status"] == "error"
    assert "must include both node name and output field name" in result["error_message"]


def test_check_rejects_reference_to_missing_output_field():
    wf = _minimal_valid_workflow()
    wf["node_2"]["input_fields"]["x"]["reference"] = "__start__.missing"

    result = Workflow.check(wf)

    assert result["status"] == "error"
    assert "invalid output field" in result["error_message"]
    assert "__start__.missing" in result["error_message"]


def test_check_rejects_reference_type_mismatch():
    wf = _minimal_valid_workflow()
    wf["node_2"]["input_fields"]["x"]["type"] = "number"

    result = Workflow.check(wf)

    assert result["status"] == "error"
    assert "type mismatch" in result["error_message"]
    assert "__start__.x" in result["error_message"]
    assert "input field type is 'number'" in result["error_message"]
    assert "referenced output field type is 'string'" in result["error_message"]


def test_check_warns_when_consuming_node_input_field_has_no_source():
    wf = _minimal_valid_workflow()
    wf["node_1"]["children"] = ["node_3"]
    wf["node_3"] = {
        "node_id": "node_3",
        "node_name": "normalize",
        "node_type": "CodeNode",
        "node_description": "normalize input",
        "input_fields": {
            "unused_text": {"type": "string", "value": "", "reference": ""},
        },
        "output_fields": {
            "x": {"type": "string", "description": "normalized text"},
        },
        "node_config": {
            "programming_language": "python",
            "process_fn": "def process_fn(inputs):\n    return {'x': ''}\n",
        },
        "children": ["node_2"],
        "__attributes__": {"x": 200, "y": 0},
    }
    wf["node_2"]["input_fields"]["x"]["reference"] = "normalize.x"

    result = Workflow.check(wf)

    assert result["status"] == "success"
    warnings = result.get("warnings") or []
    assert len(warnings) == 1
    assert warnings[0]["node_id"] == "global"
    assert warnings[0]["kind"] == "empty_input_sources"
    assert warnings[0]["fields"] == ["node_3.unused_text"]
    assert "not effective" in warnings[0]["message"]
    assert "remove unused input fields" in warnings[0]["message"]
    assert "node_3.unused_text" in warnings[0]["message"]


def test_check_does_not_warn_for_end_node_empty_input_without_source():
    wf = _minimal_valid_workflow()
    wf["node_2"]["input_fields"]["x"]["reference"] = ""

    result = Workflow.check(wf)

    assert result == {"status": "success"}


def test_check_rejects_orphaned_node():
    """A node not reachable from StartNode should fail reachability check."""
    wf = _minimal_valid_workflow()
    wf["node_orphan"] = copy.deepcopy(wf["node_2"])
    wf["node_orphan"]["node_id"] = "node_orphan"
    wf["node_orphan"]["node_name"] = "__end_orphan__"
    result = Workflow.check(wf)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Per-node check() wired into the route Workflow.check (Stream-Engine).
#
# These cases are STRUCTURALLY valid (single Start, full reachability,
# children-DAG, paired/parent-count rules all satisfied) but violate a
# per-node invariant that lives in the node class's own check() — formerly
# enforced ONLY on the agent path. After wiring, the route Check rejects them.
# ---------------------------------------------------------------------------


def _condition_workflow() -> dict:
    """Start → Condition → (End_a | End_b). A fully-valid 4-node graph.

    The ConditionNode maps both children plus a mandatory "others" fallback.
    """
    return {
        "__meta__": {
            "workflow_id": "wf_cond",
            "workflow_name": "cond",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {"n": {"type": "integer", "value": 0, "reference": ""}},
            "output_fields": {"n": {"type": "integer", "description": "n"}},
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "router",
            "node_type": "ConditionNode",
            "node_description": "route",
            "input_fields": {"n": {"type": "integer", "value": 0, "reference": "__start__.n"}},
            "output_fields": {"condition": {"type": "string", "description": "which branch"}},
            "node_config": {
                "conditions": [
                    {"condition_name": "pos", "condition_str": "{n} > 0", "next_node_id": "node_3"},
                    {"condition_name": "others", "condition_str": "others", "next_node_id": "node_4"},
                ]
            },
            "children": ["node_3", "node_4"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "branch_a",
            "node_type": "CodeNode",
            "node_description": "branch a",
            "input_fields": {},
            "output_fields": {"r": {"type": "integer", "description": "r"}},
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'r': 1}",
            },
            "children": [],
        },
        "node_4": {
            "node_id": "node_4",
            "node_name": "branch_b",
            "node_type": "CodeNode",
            "node_description": "branch b",
            "input_fields": {},
            "output_fields": {"r": {"type": "integer", "description": "r"}},
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'r': 2}",
            },
            "children": [],
        },
    }


def _parallel_workflow() -> dict:
    """Start → ParallelStart → (Code_a ∥ Code_b) → ParallelEnd → End. Fully valid."""
    return {
        "__meta__": {
            "workflow_id": "wf_par",
            "workflow_name": "par",
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
            "node_name": "fork",
            "node_type": "ParallelStartNode",
            "node_description": "fork",
            "input_fields": {},
            "output_fields": {},
            "node_config": {
                "parallel_end_node_id": "node_5",
                "branches": {
                    "a": {"branch_description": "a", "next_node_id": "node_3"},
                    "b": {"branch_description": "b", "next_node_id": "node_4"},
                },
            },
            "children": ["node_3", "node_4"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "work_a",
            "node_type": "CodeNode",
            "node_description": "a",
            "input_fields": {},
            "output_fields": {"r": {"type": "integer", "description": "r"}},
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'r': 1}",
            },
            "children": ["node_5"],
        },
        "node_4": {
            "node_id": "node_4",
            "node_name": "work_b",
            "node_type": "CodeNode",
            "node_description": "b",
            "input_fields": {},
            "output_fields": {"r": {"type": "integer", "description": "r"}},
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'r': 2}",
            },
            "children": ["node_5"],
        },
        "node_5": {
            "node_id": "node_5",
            "node_name": "join",
            "node_type": "ParallelEndNode",
            "node_description": "join",
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
            "input_fields": {},
            "output_fields": {},
            "node_config": {},
            "children": [],
        },
    }


def test_check_accepts_valid_condition_workflow():
    assert Workflow.check(_condition_workflow()) == {"status": "success"}


def test_check_accepts_condition_cards_with_builder_state_fields():
    """Condition cards may carry restore-only builder metadata
    (advanced/field/operator/value). The engine ignores them at runtime but
    Workflow.check (whose card schema is additionalProperties:False) must
    still accept them as optional properties.
    """
    wf = _condition_workflow()
    wf["node_2"]["node_config"]["conditions"][0].update(
        {"advanced": False, "field": "n", "operator": ">", "value": "0"}
    )
    wf["node_2"]["node_config"]["conditions"][1].update({"advanced": True})
    assert Workflow.check(wf) == {"status": "success"}


def test_check_rejects_condition_advanced_mixed_with_builder_fields():
    wf = _condition_workflow()
    wf["node_2"]["node_config"]["conditions"][0].update(
        {"advanced": True, "field": "n", "operator": ">", "value": "0"}
    )

    result = Workflow.check(wf)

    assert result["status"] == "error", result
    assert "advanced=true" in result["error_message"]
    assert "Remove builder field" in result["error_message"]


def test_check_rejects_condition_builder_missing_parts():
    wf = _condition_workflow()
    wf["node_2"]["node_config"]["conditions"][0].update(
        {"advanced": False, "field": "n", "operator": ">", "value": ""}
    )

    result = Workflow.check(wf)

    assert result["status"] == "error", result
    assert "advanced=false" in result["error_message"]
    assert "field/operator/value" in result["error_message"]


def test_check_accepts_valid_parallel_workflow():
    assert Workflow.check(_parallel_workflow()) == {"status": "success"}


def test_check_rejects_shared_end_node_with_branch_specific_message():
    wf = _condition_workflow()
    end = {
        "node_id": "node_5",
        "node_name": "__end__",
        "node_type": "EndNode",
        "node_description": "shared end",
        "input_fields": {},
        "output_fields": {},
        "node_config": {},
        "children": [],
    }
    wf["node_3"]["children"] = ["node_5"]
    wf["node_4"]["children"] = ["node_5"]
    wf["node_5"] = end

    result = Workflow.check(wf)

    assert result["status"] == "error", result
    assert "EndNode 'node_5' has 2 parent(s); allows ≤ 1." in result["error_message"]
    assert "Different terminal branches must use different EndNode nodes" in result["error_message"]
    assert "do not share one EndNode across branches" in result["error_message"]


def test_check_rejects_condition_next_node_ids_not_equal_children():
    """conditions' non-empty next_node_ids must EXACTLY equal children.

    Structurally valid (node_4 is still a real child + reachable), but the
    Condition no longer maps node_4 → per-node check fails. Previously passed.
    """
    wf = _condition_workflow()
    # Re-point the "others" fallback off node_4 so node_4 is an unmapped child.
    wf["node_2"]["node_config"]["conditions"][1]["next_node_id"] = "node_3"
    result = Workflow.check(wf)
    assert result["status"] == "error", result
    assert "node_2" in result["error_message"]
    assert "node_4" in result["error_message"] or "children" in result["error_message"]


def test_check_rejects_condition_missing_others_fallback():
    """A ConditionNode without an 'others' fallback fails per-node check."""
    wf = _condition_workflow()
    wf["node_2"]["node_config"]["conditions"][1]["condition_name"] = "neg"
    wf["node_2"]["node_config"]["conditions"][1]["condition_str"] = "{n} <= 0"
    result = Workflow.check(wf)
    assert result["status"] == "error", result
    assert "node_2" in result["error_message"]
    # The mandatory "others" fallback is enforced by ConditionNode.check
    # (its CONFIG_SCHEMA `contains` rule + an explicit assert). Either path
    # produces a ConditionNode-attributed error.
    assert "ConditionNode" in result["error_message"]


def test_check_rejects_condition_fallback_with_nonliteral_condition_str():
    """The fallback must be recognized by both name and literal condition string."""
    wf = _condition_workflow()
    wf["node_2"]["node_config"]["conditions"][1]["condition_str"] = "{n} <= 0"
    result = Workflow.check(wf)
    assert result["status"] == "error", result
    assert "node_2" in result["error_message"]
    assert "ConditionNode" in result["error_message"]
    assert (
        "condition_name='others' and condition_str='others'" in result["error_message"]
        or "does not contain items matching the given schema" in result["error_message"]
    )


def test_check_rejects_condition_renamed_fallback_with_clear_message():
    wf = _condition_workflow()
    wf["node_2"]["node_config"]["conditions"][1]["condition_name"] = "negative"

    result = Workflow.check(wf)

    assert result["status"] == "error", result
    assert "ConditionNode" in result["error_message"]
    assert (
        "condition_name='others' and condition_str='others'" in result["error_message"]
        or "does not contain items matching the given schema" in result["error_message"]
    )


def test_check_rejects_condition_fallback_not_last():
    wf = _condition_workflow()
    conditions = wf["node_2"]["node_config"]["conditions"]
    conditions.reverse()

    result = Workflow.check(wf)

    assert result["status"] == "error", result
    assert "fallback rule must be the final item" in result["error_message"]


def test_check_rejects_parallel_branches_not_equal_children():
    """ParallelStart branches' non-empty next_node_ids must equal children."""
    wf = _parallel_workflow()
    # Drop branch "b" → node_4 is an unmapped child.
    del wf["node_2"]["node_config"]["branches"]["b"]
    result = Workflow.check(wf)
    assert result["status"] == "error", result
    assert "node_2" in result["error_message"]


def test_check_does_not_crash_on_node_without_per_node_check(monkeypatch):
    """A node type that inherits BaseNode.check (raises NotImplementedError)
    must be skipped gracefully, not crash the whole Check."""
    from vibecanvas_engine.node import node_registry, BaseNode

    # KnowledgeSearchNode (api engine_nodes) is the real-world example; here we
    # register a throwaway node type that inherits the default check to prove
    # the skip logic without importing the api package.
    class _NoCheckNode(BaseNode):
        pass

    monkeypatch.setitem(node_registry._module_dict, "_NoCheckNode", _NoCheckNode)
    assert _NoCheckNode.check is BaseNode.check  # sanity: inherits default

    wf = _minimal_valid_workflow()
    wf["node_1"]["children"] = ["node_2", "node_3"]
    wf["node_3"] = {
        "node_id": "node_3",
        "node_name": "ext",
        "node_type": "_NoCheckNode",
        "node_description": "ext",
        "input_fields": {},
        "output_fields": {},
        "node_config": {},
        "children": [],
    }
    # node_3 inherits BaseNode.check → skipped, not crashed. (Structural rules:
    # CodeNode/unknown parent-count rule is None for an unregistered-in-PARENT_RULES
    # type, so this stays a clean skip path.)
    result = Workflow.check(wf)
    # Either success, or a structural error — but NEVER a NotImplementedError crash.
    assert result["status"] in ("success", "error")
    assert "NotImplementedError" not in result.get("error_message", "")

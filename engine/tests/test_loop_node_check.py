from __future__ import annotations

import copy

from vibecanvas_engine.nodes.loop import LoopBeginNode


def _loop_begin_node() -> dict:
    return {
        "node_id": "node_2",
        "node_name": "loop_start",
        "node_type": "LoopBeginNode",
        "node_description": "Loop over indexed items",
        "input_fields": {},
        "output_fields": {
            "loop_output": {
                "type": "array",
                "description": "Completed iteration snapshots",
            },
            "i": {
                "type": "integer",
                "description": "Current loop index",
            },
        },
        "node_config": {
            "init_value": {"value": 0, "reference": ""},
            "step_value": 1,
            "end_value": {"value": 5, "reference": ""},
            "loop_end_node_id": "node_4",
        },
        "children": ["node_3"],
        "__attributes__": {"x": 200, "y": 0},
    }


def test_loop_begin_check_accepts_required_outputs():
    result = LoopBeginNode.check(_loop_begin_node())
    assert result["status"] == "success"


def test_loop_begin_check_rejects_loop_output_type_mismatch():
    node = copy.deepcopy(_loop_begin_node())
    node["output_fields"]["loop_output"]["type"] = "object"
    result = LoopBeginNode.check(node)
    assert result["status"] == "error"
    assert "loop_output type must be 'array'" in result["error_message"]


def test_loop_begin_check_rejects_i_type_mismatch():
    node = copy.deepcopy(_loop_begin_node())
    node["output_fields"]["i"]["type"] = "number"
    result = LoopBeginNode.check(node)
    assert result["status"] == "error"
    assert "output_fields.i type must be 'integer'" in result["error_message"]


def test_loop_begin_check_rejects_output_field_drift():
    node = copy.deepcopy(_loop_begin_node())
    node["output_fields"]["extra"] = {"type": "string", "description": "unexpected"}
    result = LoopBeginNode.check(node)
    assert result["status"] == "error"
    assert "output_fields must be exactly" in result["error_message"]

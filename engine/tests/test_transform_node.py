# -*- coding: utf-8 -*-
"""TransformNode — N→M per-output mapping model.

Each output field picks one input field and folds an ordered transform_list
(path/index/length/cast/default/compute/pick) over its value.
"""

from vibecanvas_engine.nodes.transform import TransformNode, _apply_value_op


def _node(mappings, output_fields, input_fields=None):
    return TransformNode(
        node_id="node_1",
        node_name="xform",
        node_type="TransformNode",
        node_description="",
        input_fields=input_fields or {},
        output_fields=output_fields,
        node_config={"mappings": mappings},
        children=[],
    )


def test_extract_three_fields_from_one_complex_dict():
    """The headline use case: one complex input dict → three extracted outputs."""
    node = _node(
        mappings=[
            {
                "input_field": "api",
                "output_field": "user_name",
                "transform_list": [
                    {"op": "path", "path": "data.user.name"},
                    {"op": "cast", "to": "string"},
                ],
            },
            {
                "input_field": "api",
                "output_field": "item_count",
                "transform_list": [
                    {"op": "path", "path": "data.items"},
                    {"op": "length"},
                ],
            },
            {
                "input_field": "api",
                "output_field": "first_id",
                "transform_list": [
                    {"op": "path", "path": "data.items"},
                    {"op": "index", "index": 0},
                    {"op": "path", "path": "id"},
                    {"op": "cast", "to": "integer"},
                ],
            },
        ],
        output_fields={
            "user_name": {"type": "string", "description": ""},
            "item_count": {"type": "integer", "description": ""},
            "first_id": {"type": "integer", "description": ""},
        },
    )
    inputs = {
        "api": {
            "data": {
                "user": {"name": "Ann"},
                "items": [{"id": "7"}, {"id": "9"}],
            }
        }
    }
    result = node(inputs, {})
    assert result["output"] == {
        "user_name": "Ann",
        "item_count": 2,
        "first_id": 7,
    }


def test_cast_and_default():
    node = _node(
        mappings=[
            {"input_field": "raw", "output_field": "score", "transform_list": [{"op": "cast", "to": "number"}]},
            {"input_field": "tier", "output_field": "tier", "transform_list": [{"op": "default", "value": "bronze"}]},
        ],
        output_fields={"score": {"type": "number", "description": ""}, "tier": {"type": "string", "description": ""}},
    )
    result = node({"raw": "3.5", "tier": None}, {})
    assert result["output"] == {"score": 3.5, "tier": "bronze"}


def test_empty_transform_list_is_passthrough():
    node = _node(
        mappings=[{"input_field": "x", "output_field": "y", "transform_list": []}],
        output_fields={"y": {"type": "string", "description": ""}},
    )
    assert node({"x": "hello"}, {})["output"] == {"y": "hello"}


def test_only_declared_outputs_are_returned():
    """A mapping whose output_field isn't declared is filtered out (defensive)."""
    node = _node(
        mappings=[
            {"input_field": "x", "output_field": "kept", "transform_list": []},
            {"input_field": "x", "output_field": "stray", "transform_list": []},
        ],
        output_fields={"kept": {"type": "string", "description": ""}},
    )
    out = node({"x": "v"}, {})["output"]
    assert out == {"kept": "v"}
    assert "stray" not in out


def test_pick_and_compute_value_ops():
    assert _apply_value_op({"a": 1, "b": 2, "c": 3}, {"op": "pick", "fields": ["a", "c"]}) == {"a": 1, "c": 3}
    assert _apply_value_op(10, {"op": "compute", "expr": "{value} * 2"}) == 20
    # Stored workflows created before ``expr`` became canonical still execute.
    assert _apply_value_op(10, {"op": "compute", "expression": "{value} + 5"}) == 15
    assert _apply_value_op([5, 6, 7], {"op": "index", "index": -1}) == 7
    assert _apply_value_op([5, 6, 7], {"op": "length"}) == 3
    assert _apply_value_op(None, {"op": "default", "value": 42}) == 42
    # non-dict pick passes through
    assert _apply_value_op("str", {"op": "pick", "fields": ["a"]}) == "str"


def test_check_accepts_new_mappings_shape_and_rejects_bad_op():
    good = {
        "node_id": "node_1", "node_name": "x", "node_type": "TransformNode",
        "node_description": "", "input_fields": {}, "children": [],
        "output_fields": {"y": {"type": "string", "description": ""}},
        "node_config": {"mappings": [
            {"input_field": "a", "output_field": "y", "transform_list": [{"op": "path", "path": "k"}]}
        ]},
    }
    good["input_fields"] = {"a": {"type": "object", "value": {}, "reference": "source.a"}}
    res = TransformNode.check(good)
    assert res["status"] == "success", res

    bad = {
        **good,
        "node_config": {"mappings": [
            {"input_field": "a", "output_field": "y", "transform_list": [{"op": "rename"}]}
        ]},
    }
    res_bad = TransformNode.check(bad)
    assert res_bad["status"] == "error"
    # Rejected by the op enum (jsonschema) — the invalid op name is surfaced.
    assert "rename" in res_bad["error_message"]


def test_check_rejects_mapping_input_not_declared():
    node = {
        "node_id": "node_1", "node_name": "x", "node_type": "TransformNode",
        "node_description": "", "input_fields": {}, "children": [],
        "output_fields": {"y": {"type": "string", "description": ""}},
        "node_config": {"mappings": [
            {"input_field": "missing", "output_field": "y", "transform_list": []}
        ]},
    }
    res = TransformNode.check(node)
    assert res["status"] == "error"
    assert "input_field 'missing' must exist" in res["error_message"]


def test_check_rejects_mapping_output_not_declared_or_duplicate():
    base = {
        "node_id": "node_1", "node_name": "x", "node_type": "TransformNode",
        "node_description": "",
        "input_fields": {"a": {"type": "string", "value": "", "reference": "source.a"}},
        "children": [],
        "output_fields": {"y": {"type": "string", "description": ""}},
        "node_config": {"mappings": [
            {"input_field": "a", "output_field": "z", "transform_list": []}
        ]},
    }
    res = TransformNode.check(base)
    assert res["status"] == "error"
    assert "mappings must not produce undeclared outputs" in res["error_message"]

    duplicate = {
        **base,
        "node_config": {"mappings": [
            {"input_field": "a", "output_field": "y", "transform_list": []},
            {"input_field": "a", "output_field": "y", "transform_list": []},
        ]},
    }
    res_dup = TransformNode.check(duplicate)
    assert res_dup["status"] == "error"
    assert "exactly one mapping" in res_dup["error_message"]

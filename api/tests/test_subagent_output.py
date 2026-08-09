"""SubAgent output projection helpers."""

from vibecanvas_api.agents.tools.subagent.output import coerce_to_fields


def test_coerce_maps_declared_fields_with_defaults():
    spec = {"a": {"type": "string"}, "b": {"type": "integer"}}
    assert coerce_to_fields({"a": "x"}, spec) == {"a": "x", "b": ""}
    # extra key not in spec is dropped
    assert coerce_to_fields({"a": "x", "z": 9}, spec) == {"a": "x", "b": ""}


def test_coerce_json_encodes_complex_values():
    spec = {"a": {"type": "string"}}
    assert coerce_to_fields({"a": {"nested": 1}}, spec)["a"] == '{"nested": 1}'

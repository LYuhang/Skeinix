from __future__ import annotations

import pytest

from vibecanvas_engine.utils import (
    InputNormalizeError,
    normalize_inputs_for_fields,
    normalize_value_for_type,
    start_node_input_fields,
)


def test_normalizes_json_and_python_literals() -> None:
    assert normalize_value_for_type('["u1","u2"]', "list") == ["u1", "u2"]
    assert normalize_value_for_type("['u1','u2']", "array") == ["u1", "u2"]
    assert normalize_value_for_type("{'a': 1, 'ok': True}", "dict") == {
        "a": 1,
        "ok": True,
    }
    assert normalize_value_for_type('{"a": 1}', "object") == {"a": 1}
    assert normalize_value_for_type(("a", "b"), "tuple") == ["a", "b"]


def test_already_typed_values_pass_through() -> None:
    list_value = ["u1", "u2"]
    dict_value = {"a": 1}
    assert normalize_value_for_type(list_value, "list") is list_value
    assert normalize_value_for_type(dict_value, "dict") is dict_value
    assert normalize_value_for_type(True, "boolean") is True
    assert normalize_value_for_type(3, "integer") == 3
    assert normalize_value_for_type(3.5, "number") == 3.5
    assert normalize_value_for_type("x", "string") == "x"


def test_normalizes_scalars() -> None:
    assert normalize_value_for_type("3", "integer") == 3
    assert normalize_value_for_type("3.5", "number") == 3.5
    assert normalize_value_for_type("True", "boolean") is True
    assert normalize_value_for_type("false", "bool") is False
    assert normalize_value_for_type(42, "string") == "42"


def test_rejects_invalid_structured_values() -> None:
    with pytest.raises(InputNormalizeError):
        normalize_value_for_type("{bad", "object")
    with pytest.raises(InputNormalizeError):
        normalize_value_for_type('{"a": 1}', "array")
    with pytest.raises(InputNormalizeError):
        normalize_value_for_type("[1, 2]", "object")


def test_normalizes_known_fields_and_preserves_extras() -> None:
    out = normalize_inputs_for_fields(
        {"urls": "['a']", "n": "3", "extra": "x"},
        {
            "urls": {"type": "array"},
            "n": {"type": "integer"},
        },
    )
    assert out == {"urls": ["a"], "n": 3, "extra": "x"}


def test_errors_include_field_name() -> None:
    with pytest.raises(InputNormalizeError, match="urls"):
        normalize_inputs_for_fields(
            {"urls": "not-a-list"},
            {"urls": {"type": "array"}},
        )


def test_extracts_start_node_input_fields() -> None:
    workflow = {
        "node_1": {
            "node_type": "StartNode",
            "input_fields": {"items": {"type": "list"}},
        },
        "__meta__": {},
    }
    assert start_node_input_fields(workflow) == {"items": {"type": "list"}}

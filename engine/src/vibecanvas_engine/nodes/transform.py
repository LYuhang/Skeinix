# -*- coding: utf-8 -*-
"""TransformNode — no-code N→M data mapping.

Each OUTPUT field is produced by its own mapping: pick ONE input field, then
run its value through an ordered ``transform_list`` (each op transforms the
*running value*). Engine: for each mapping, ``value = inputs[input_field]``;
fold the transform_list over it; assign the result to ``output_field`` (only
declared ``output_fields`` are returned).

The mapping/op catalog and a worked example live in ``AGENT_SPEC`` below.
"""

from copy import deepcopy

import jsonschema

from ..utils import safe_call_with_args, recursive_get
from ..sandbox import PythonSandbox
from ..register import node_registry
from .base import BaseNode


_TRANSFORM_SANDBOX = PythonSandbox({"math": "math", "re": "re"})

# Value-level ops — each takes the running VALUE and returns the next value.
_VALID_OPS = {"path", "index", "length", "cast", "default", "compute", "pick"}

_CAST_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes"),
}


def _apply_value_op(value, op_cfg: dict):
    """Apply ONE value-level transform op to ``value``; return the new value."""
    op = op_cfg["op"]

    if op == "path":
        # Dot-bracket navigation INTO the current value (dict/list). Empty path
        # is the identity. e.g. "data.user.name", "items[0].id".
        path_expr = op_cfg.get("path", "")
        if not path_expr:
            return value
        return recursive_get(value, path_expr)

    if op == "index":
        # Nth element of a list/tuple (negative allowed); None when out of range
        # or not a sequence.
        idx = int(op_cfg.get("index", 0))
        if isinstance(value, (list, tuple)) and -len(value) <= idx < len(value):
            return value[idx]
        return None

    if op == "length":
        # Length of a list/tuple/str/dict; 0 otherwise.
        if isinstance(value, (list, tuple, str, dict)):
            return len(value)
        return 0

    if op == "cast":
        to_type = op_cfg.get("to", "string")
        converter = _CAST_MAP.get(to_type, str)
        return converter(value)

    if op == "default":
        # Fall back to a constant when the running value is None.
        return op_cfg.get("value") if value is None else value

    if op == "compute":
        # Expression over the running value via the {value} placeholder, run in
        # the math/re sandbox. e.g. "{value} * 2", "round({value}, 2)".
        # ``expression`` was emitted by an older agent spec/editor.  Accept it
        # for stored-workflow compatibility; all current writers normalize to
        # the canonical, shorter ``expr`` key.
        expr = op_cfg.get("expr", op_cfg.get("expression", ""))
        eval_str = expr.replace("{value}", repr(value))
        return _TRANSFORM_SANDBOX.evaluate(eval_str)

    if op == "pick":
        # Keep only the listed sub-keys of a dict value → a smaller dict.
        # Non-dict values pass through unchanged.
        if isinstance(value, dict):
            fields = op_cfg.get("fields", []) or []
            return {k: value[k] for k in fields if k in value}
        return value

    return value


@node_registry.register()
class TransformNode(BaseNode):
    """No-code N→M data mapping: each output field is produced by picking one
    input field and folding an ordered transform_list (path/index/length/cast/
    default/compute/pick) over its value."""

    CONFIG_SCHEMA = {
        "type": "object",
        "required": ["mappings"],
        "properties": {
            "mappings": {
                "type": "array",
                "description": "One mapping per output field: input_field → transform_list → output_field.",
                "items": {
                    "type": "object",
                    "required": ["input_field", "output_field", "transform_list"],
                    "properties": {
                        "input_field": {
                            "type": "string",
                            "description": "The input field whose value seeds this output's transform chain.",
                        },
                        "output_field": {
                            "type": "string",
                            "description": "The declared output field this mapping produces.",
                        },
                        "transform_list": {
                            "type": "array",
                            "description": "Ordered value-level ops; each transforms the running value.",
                            "items": {
                                "type": "object",
                                "required": ["op"],
                                "properties": {
                                    "op": {
                                        "type": "string",
                                        "enum": ["path", "index", "length", "cast", "default", "compute", "pick"],
                                    }
                                },
                            },
                        },
                    },
                    "additionalProperties": False,
                },
            }
        },
        "additionalProperties": False,
    }

    AGENT_SPEC = {
        "summary": "Map input fields to output fields with ordered no-code value transforms.",
        "when_to_use": "Use for simple extraction, reshaping, type conversion, defaults, lengths, indexing, picking dict keys, or small arithmetic without writing Python.",
        "when_not_to_use": "For cross-field logic, loops, or filtering use CodeNode. For LLM processing use PromptNode.",
        "constraints": [
            "Use node_type='TransformNode' and at most one child.",
            "Each declared output_field must have exactly one mapping.",
            "Each mapping starts from one declared input_field, applies transform_list in order to the running value, then writes one declared output_field.",
            "Available ops: path, index, length, cast, default, compute, pick.",
            "path navigates dot/bracket paths inside the running value; index selects a list item; length returns len(list/string/dict); cast converts type; default replaces None; compute uses exactly {'op': 'compute', 'expr': '<expression containing {value}>'}; pick keeps selected dict keys.",
            "Use CodeNode instead when the transform depends on multiple fields at once, needs filtering/loops, or needs complex validation."
        ],
        "config_guide": {
            "mappings": "Array of {input_field, output_field, transform_list}. One mapping per output field; empty transform_list passes the input through unchanged.",
            "compute": "Use the canonical key expr, for example {'op': 'compute', 'expr': '\"Fetched: \" + {value}'}. Do not use an expression key.",
        },
        "examples": [
            {
                "scenario": "Extract three fields out of one complex API response dict",
                "node_dict": {
                    "node_id": "node_4",
                    "node_name": "extract_data",
                    "node_type": "TransformNode",
                    "node_description": "Pull name, item count, and first item id from a nested response",
                    "input_fields": {
                        "api_response": {"type": "object", "value": {}, "reference": "fetch_user.response_body"}
                    },
                    "output_fields": {
                        "user_name": {"type": "string", "description": "Extracted user name"},
                        "item_count": {"type": "integer", "description": "Number of items"},
                        "first_id": {"type": "integer", "description": "First item's id"},
                    },
                    "node_config": {
                        "mappings": [
                            {
                                "input_field": "api_response",
                                "output_field": "user_name",
                                "transform_list": [
                                    {"op": "path", "path": "data.user.name"},
                                    {"op": "cast", "to": "string"},
                                ],
                            },
                            {
                                "input_field": "api_response",
                                "output_field": "item_count",
                                "transform_list": [
                                    {"op": "path", "path": "data.items"},
                                    {"op": "length"},
                                ],
                            },
                            {
                                "input_field": "api_response",
                                "output_field": "first_id",
                                "transform_list": [
                                    {"op": "path", "path": "data.items"},
                                    {"op": "index", "index": 0},
                                    {"op": "path", "path": "id"},
                                    {"op": "cast", "to": "integer"},
                                ],
                            },
                        ]
                    },
                    "children": ["node_5"],
                    "__attributes__": {"x": 400, "y": 0},
                },
            },
        ],
        "display": {
            "name": {"en": "TransformNode", "zh": "数据转换节点"},
            "description": {"en": "No-code N→M data mapping with per-output transform chains", "zh": "无代码 N→M 数据映射，每个输出一条转换链"},
            "icon": "transform",
            "category": {"en": "Data Processing", "zh": "数据处理"},
        },
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    @safe_call_with_args(prefix="[TransformNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(instance=node_dict, schema=BaseNode.GENERAL_NODE_SCHEMA)

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = TransformNode.CONFIG_SCHEMA
        jsonschema.validate(instance=node_dict, schema=specific_schema)

        jsonschema.validate(instance=node_dict, schema={
            "type": "object",
            "properties": {
                "node_type": {"const": "TransformNode"},
                "children": {"type": "array", "maxItems": 1}
            }
        })

        mappings = node_dict["node_config"].get("mappings", [])
        input_fields = set(node_dict.get("input_fields", {}).keys())
        output_fields = set(node_dict.get("output_fields", {}).keys())
        mapped_outputs = [mapping.get("output_field") for mapping in mappings]

        assert set(mapped_outputs) == output_fields and len(mapped_outputs) == len(output_fields), (
            "For TransformNode, each declared output_field must have exactly "
            "one mapping, and mappings must not produce undeclared outputs."
        )

        for i, mapping in enumerate(mappings):
            input_field = mapping.get("input_field")
            output_field = mapping.get("output_field")
            assert input_field in input_fields, (
                f"For TransformNode, mappings[{i}].input_field '{input_field}' "
                "must exist in input_fields."
            )
            assert output_field in output_fields, (
                f"For TransformNode, mappings[{i}].output_field '{output_field}' "
                "must exist in output_fields."
            )
            for j, op_cfg in enumerate(mapping.get("transform_list", [])):
                op = op_cfg.get("op")
                assert op in _VALID_OPS, (
                    f"mappings[{i}].transform_list[{j}].op '{op}' is not valid. "
                    f"Must be one of: {_VALID_OPS}"
                )

    @safe_call_with_args(prefix="[TransformNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        result = {}
        for mapping in self.node_config["mappings"]:
            value = inputs.get(mapping.get("input_field"))
            for op_cfg in mapping.get("transform_list", []):
                value = _apply_value_op(value, op_cfg)
            result[mapping["output_field"]] = value
        # Defensive: only return declared output fields.
        output_keys = set(self.output_fields.keys())
        return {k: v for k, v in result.items() if k in output_keys}

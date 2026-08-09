# -*- coding: utf-8 -*-
"""StartNode — the entry point of a workflow."""

from copy import deepcopy

import jsonschema

from ..utils import safe_call_with_args
from ..register import node_registry
from .base import BaseNode


_COMPLEX_FIELD_TYPES = {"array", "object"}


def _validate_schema_expanded_to_leaves(schema: dict, path: str = "schema") -> None:
    """Assert every nested array/object schema is expanded down to leaf types."""
    assert isinstance(schema, dict), f"{path} must be an object schema."
    schema_type = schema.get("type")
    assert isinstance(schema_type, str) and schema_type, f"{path}.type is required."

    if schema_type == "array":
        items = schema.get("items")
        assert isinstance(items, dict) and items, f"{path}.items is required for array schemas."
        _validate_schema_expanded_to_leaves(items, f"{path}.items")
        return

    if schema_type == "object":
        properties = schema.get("properties")
        assert isinstance(properties, dict) and properties, f"{path}.properties is required for object schemas."
        for prop_name, prop_schema in properties.items():
            _validate_schema_expanded_to_leaves(
                prop_schema,
                f"{path}.properties.{prop_name}",
            )
        return


@node_registry.register()
class StartNode(BaseNode):
    """StartNode — the workflow's entry point. Passes the external input dict through unchanged.

    Authoring constraints live in ``AGENT_SPEC``; ``node_config`` is always empty (``{}``).
    """
    CONFIG_SCHEMA = {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }

    AGENT_SPEC = {
        "summary": "Entry point of workflow. Defines the external input contract and outputs those fields unchanged. Its input fields correspond to the key/value structure of the incoming data dictionary.",
        "when_to_use": "Every workflow must have exactly one StartNode. Use it to define the workflow input interface consumed by downstream nodes.",
        "when_not_to_use": "Never create multiple StartNodes. Never use StartNode for data processing.",
        "constraints": [
            "output_fields must mirror input_fields (same field names and types).",
            "Incoming data is passed field-by-field from the input dictionary; define the dictionary's internal fields instead of one broad wrapper object unless the object is intentionally opaque.",
            "For any array/object input field, include a detailed schema; if nested array/object values appear, recursively define their items/properties until every leaf field has a primitive type.",
            "StartNode is the unique graph entry point; nothing upstream may point to it."
        ],
        "config_guide": {},
        "examples": [
            {
                "scenario": "Common multimodal input fields",
                "node_dict": {
                    "node_id": "node_1",
                    "node_name": "__start__",
                    "node_type": "StartNode",
                    "node_description": "Workflow entry point",
                    "input_fields": {
                        "input_text": {"type": "string", "value": "", "reference": ""},
                        "input_images": {
                            "type": "array",
                            "value": [],
                            "reference": "",
                            "schema": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "input_info": {
                            "type": "object",
                            "value": {},
                            "reference": "",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "source": {"type": "string"},
                                    "locale": {"type": "string"}
                                },
                                "additionalProperties": False
                            }
                        }
                    },
                    "output_fields": {
                        "input_text": {"type": "string", "description": "Input text"},
                        "input_images": {"type": "array", "description": "Input image references"},
                        "input_info": {"type": "object", "description": "Additional input metadata"}
                    },
                    "node_config": {},
                    "children": ["node_2"],
                    "__attributes__": {"x": 0, "y": 0}
                }
            }
        ],
        "display": {
            "name": {"en": "StartNode", "zh": "开始节点"},
            "description": {"en": "Workflow entry point that receives external input", "zh": "工作流入口，接收外部输入"},
            "icon": "start",
            "category": {"en": "Flow Control", "zh": "流程控制"},
        }
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    @safe_call_with_args(prefix="[StartNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(
            instance=node_dict,
            schema=BaseNode.GENERAL_NODE_SCHEMA
        )

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = StartNode.CONFIG_SCHEMA
        jsonschema.validate(
            instance=node_dict,
            schema=specific_schema
        )

        jsonschema.validate(
            instance=node_dict,
            schema={
                "type": "object",
                "properties": {
                    "node_name": {
                        "const": "__start__"
                    },
                    "node_type": {
                        "const": "StartNode"
                    },
                    "children": {
                        "type": "array",
                        "maxItems": 1
                    }
                }
            }
        )

        for field_name, field_info in node_dict["input_fields"].items():
            assert field_name in node_dict["output_fields"], f"For StartNode, the output_fields should have the same field names as input_fields, but got '{field_name}' in input_fields that is not defined in output_fields."
            assert field_info["type"] == node_dict["output_fields"][field_name]["type"], f"For StartNode, the output_fields should have the same field types as input_fields, but got different types for field '{field_name}': input_field type is '{field_info['type']}' while output_field type is '{node_dict['output_fields'][field_name]['type']}'."
            if field_info["type"] in _COMPLEX_FIELD_TYPES:
                schema = field_info.get("schema")
                assert isinstance(schema, dict) and schema, f"For StartNode, input field '{field_name}' has type '{field_info['type']}' and must include a non-empty detailed schema."
                assert schema.get("type") == field_info["type"], f"For StartNode, input field '{field_name}' schema.type must match the field type '{field_info['type']}', but got '{schema.get('type')}'."
                _validate_schema_expanded_to_leaves(schema)

    @safe_call_with_args(prefix="[StartNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        return inputs

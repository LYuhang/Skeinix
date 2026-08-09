# -*- coding: utf-8 -*-
"""EndNode — the final exit point of a workflow."""

from copy import deepcopy

import jsonschema

from ..utils import safe_call_with_args
from ..register import node_registry
from .base import BaseNode


@node_registry.register()
class EndNode(BaseNode):
    """EndNode — a branch's exit point. Passes its input through unchanged as that branch's output.

    Authoring constraints live in ``AGENT_SPEC``; ``node_config`` is always empty (``{}``).
    """
    CONFIG_SCHEMA = {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }

    AGENT_SPEC = {
        "summary": "Final exit point of a branch. Passes input fields through unchanged as final output fields.",
        "when_to_use": "Use one EndNode to terminate each branch that should produce a user-visible final result.",
        "when_not_to_use": "Never use EndNode for data processing.",
        "constraints": [
            "node_name must be exactly '__end__' for every EndNode. Do not use branch-specific names such as 'approve_end', 'reject_end', or 'final_result'.",
            "output_fields must mirror input_fields exactly (same names and types).",
            "Each terminal branch owns its own EndNode. Do not merge multiple branches by pointing them to the same EndNode.",
            "If branches must merge before terminating, route them through a proper join node such as ParallelEndNode or LoopEndNode."
        ],
        "config_guide": {},
        "examples": [
            {
                "scenario": "Simple workflow exit collecting final result",
                "node_dict": {
                    "node_id": "node_99",
                    "node_name": "__end__",
                    "node_type": "EndNode",
                    "node_description": "Workflow exit point",
                    "input_fields": {
                        "result": {"type": "string", "value": "", "reference": "analyzer.result"}
                    },
                    "output_fields": {
                        "result": {"type": "string", "description": "Final workflow output"}
                    },
                    "node_config": {},
                    "children": [],
                    "__attributes__": {"x": 600, "y": 0}
                }
            }
        ],
        "display": {
            "name": {"en": "EndNode", "zh": "结束节点"},
            "description": {"en": "Workflow exit point that outputs final result", "zh": "工作流出口，输出最终结果"},
            "icon": "end",
            "category": {"en": "Flow Control", "zh": "流程控制"},
        }
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    @safe_call_with_args(prefix="[EndNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(
            instance=node_dict,
            schema=BaseNode.GENERAL_NODE_SCHEMA
        )

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = EndNode.CONFIG_SCHEMA
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
                        "const": "__end__"
                    },
                    "node_type": {
                        "const": "EndNode"
                    },
                    "children": {
                        "type": "array",
                        "maxItems": 0
                    }
                }
            }
        )

        assert len(node_dict["input_fields"]) == len(node_dict["output_fields"]), "For EndNode, the number of input_fields and output_fields must be exactly the same."

        for field_name, field_info in node_dict["input_fields"].items():
            assert field_name in node_dict["output_fields"], f"For EndNode, the output_fields should have the same field names as input_fields, but got '{field_name}' in input_fields that is not defined in output_fields."
            assert field_info["type"] == node_dict["output_fields"][field_name]["type"], f"For EndNode, the output_fields should have the same field types as input_fields, but got different types for field '{field_name}': input_field type is '{field_info['type']}' while output_field type is '{node_dict['output_fields'][field_name]['type']}'."

    @safe_call_with_args(prefix="[EndNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        return inputs

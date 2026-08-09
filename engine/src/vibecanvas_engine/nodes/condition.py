# -*- coding: utf-8 -*-
"""ConditionNode — routes execution flow based on evaluated conditions."""

from copy import deepcopy

import jsonschema

from ..utils import safe_call_with_args
from ..sandbox import PythonSandbox
from ..register import node_registry
from .base import BaseNode


@node_registry.register()
class ConditionNode(BaseNode):
    """ConditionNode — evaluates a list of condition expressions in order and routes to the first match, with a mandatory 'others' fallback.

    Authoring constraints (conditions shape, the 'others' fallback, the single 'condition' output, conditions==children) live in ``AGENT_SPEC``.
    """
    CONFIG_SCHEMA = {
        "type": "object",
        "required": [
            "conditions"
        ],
        "properties": {
            "conditions": {
                "type": "array",
                "description": (
                    "Ordered branch rules. Runtime evaluates each non-fallback "
                    "condition_str from top to bottom and routes to the first "
                    "matched next_node_id; include one final fallback rule with "
                    "condition_name='others' and condition_str='others'."
                ),
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": [
                        "condition_name",
                        "condition_str",
                        "next_node_id"
                    ],
                    "properties": {
                        "condition_name": {
                            "type": "string",
                            "description": "Unique branch label returned as output_fields.condition when this rule matches."
                        },
                        "condition_str": {
                            "type": "string",
                            "description": (
                                "Python boolean expression with {field_name} placeholders "
                                "when advanced=true, generated expression when advanced=false, "
                                "or the literal 'others' for the default fallback. Python booleans "
                                "must be True/False, not true/false."
                            )
                        },
                        "next_node_id": {
                            "type": ["string", "null"],
                            "description": "Child node ID to route to when this rule matches; may be null while drafting a branch target."
                        },
                        # advanced/field/operator/value are editor-only restore
                        # metadata for the dropdown builder; all ignored at runtime.
                        "advanced": {
                            "type": "boolean",
                            "description": (
                                "Condition editing mode. true means advanced/raw expression mode "
                                "and condition_str is authored directly. false means builder mode "
                                "and field/operator/value define the rule; condition_str is the "
                                "generated runtime expression."
                            )
                        },
                        "field": {
                            "type": "string",
                            "description": "Builder-mode field name; required and non-empty when advanced=false."
                        },
                        "operator": {
                            "type": "string",
                            "description": "Builder-mode comparison operator; required and non-empty when advanced=false."
                        },
                        "value": {
                            "type": "string",
                            "description": "Builder-mode comparison value; required when advanced=false."
                        }
                    },
                    "additionalProperties": False
                },
                "contains": {
                    "type": "object",
                    "required": ["condition_name", "condition_str"],
                    "properties": {
                        "condition_name": {
                            "const": "others"
                        },
                        "condition_str": {
                            "const": "others"
                        },
                        "next_node_id": {
                            "type": ["string", "null"]
                        }
                    }
                }
            }
        },
        "additionalProperties": False
    }

    AGENT_SPEC = {
        "summary": "Route one upstream result into one of several downstream branches.",
        "when_to_use": "Use when previous node outputs determine the next workflow branch, and the routing logic should be visible on the canvas.",
        "when_not_to_use": "For parallel execution use ParallelStartNode. For LLM-based decisions use PromptNode followed by ConditionNode.",
        "constraints": [
            "Use ConditionNode only for visible branch routing: upstream outputs are judged by conditions, and each matched condition routes to one child branch.",
            "Write one explicit condition item for every intended business branch. Each condition_name is a unique branch label; do not hide a real branch inside the fallback.",
            "For agent-authored conditions, use advanced mode: set advanced=true and write condition_str directly. condition_str uses Python expression syntax with {field_name} placeholders, for example {score} >= 0.8, {label} == 'urgent', or {flag} == True. Python boolean literals are True/False, not true/false.",
            "The frontend has two mutually exclusive authoring modes: advanced=true uses condition_str directly; advanced=false uses field/operator/value builder fields and a generated condition_str. Do not mix advanced=true with field/operator/value.",
            "Add exactly one fallback as the final item: condition_name='others' and condition_str='others'. Do not rename it, and do not use condition_str='others' with any other condition_name.",
            "Every non-empty next_node_id must appear in children, and every child must be referenced by exactly one condition. While drafting before a target exists, use next_node_id=null and leave that target out of children until the node is created.",
            "output_fields must contain exactly one string field named 'condition'.",
        ],
        "config_guide": {
            "conditions": "Ordered array of branch rules. Agent-generated rules should use {condition_name, advanced:true, condition_str, next_node_id}; the frontend builder may use {condition_name, advanced:false, field, operator, value, condition_str, next_node_id}. First match wins. Cover all named branches explicitly, then add one final fallback item: {condition_name:'others', condition_str:'others', next_node_id:<fallback_child>}."
        },
        "examples": [
            {
                "scenario": "Route by score threshold",
                "node_dict": {
                    "node_id": "node_4",
                    "node_name": "score_router",
                    "node_type": "ConditionNode",
                    "node_description": "Route based on score threshold",
                    "input_fields": {
                        "score": {"type": "number", "value": 0, "reference": "analyzer.score"}
                    },
                    "output_fields": {
                        "condition": {"type": "string", "description": "Matched condition name"}
                    },
                    "node_config": {
                        "conditions": [
                            {"condition_name": "high", "advanced": True, "condition_str": "{score} >= 0.8", "next_node_id": "node_5"},
                            {"condition_name": "others", "condition_str": "others", "next_node_id": "node_6"}
                        ]
                    },
                    "children": ["node_5", "node_6"],
                    "__attributes__": {"x": 400, "y": 0}
                }
            }
        ],
        "display": {
            "name": {"en": "ConditionNode", "zh": "条件节点"},
            "description": {"en": "Route execution based on condition expressions", "zh": "根据条件表达式路由执行流"},
            "icon": "condition",
            "category": {"en": "Flow Control", "zh": "流程控制"},
        }
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sandbox = PythonSandbox({
            "math": "math",
            "re": "re"
        })

    @staticmethod
    @safe_call_with_args(prefix="[ConditionNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(
            instance=node_dict,
            schema=BaseNode.GENERAL_NODE_SCHEMA
        )

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = ConditionNode.CONFIG_SCHEMA
        jsonschema.validate(
            instance=node_dict,
            schema=specific_schema
        )

        jsonschema.validate(
            instance=node_dict,
            schema={
                "type": "object",
                "properties": {
                    "node_type": {
                        "const": "ConditionNode"
                    }
                }
            }
        )

        output_fields = node_dict.get("output_fields", {})
        assert len(output_fields) == 1, f"For ConditionNode, the output_fields must contain exactly 1 field, but got {len(output_fields)}."
        assert "condition" in output_fields, "For ConditionNode, the output_fields must strictly contain the 'condition' field."

        conditions = node_dict["node_config"].get("conditions", [])
        condition_names = [str(cond.get("condition_name", "")).strip() for cond in conditions]
        assert len(condition_names) == len(set(condition_names)), (
            "ConditionNode condition_name values must be unique."
        )
        fallback_indices = []
        for idx, cond in enumerate(conditions):
            cond_name = str(cond.get("condition_name", "")).strip()
            cond_str = str(cond.get("condition_str", "")).strip()
            mentions_fallback = cond_name == "others" or cond_str == "others"
            if mentions_fallback:
                assert cond_name == "others" and cond_str == "others", (
                    "ConditionNode fallback must be exactly "
                    "condition_name='others' and condition_str='others'. "
                    "Do not use condition_str='others' for a named business branch."
                )
                fallback_indices.append(idx)
                continue

            assert cond_str, (
                f"ConditionNode condition '{cond_name or idx}' must have a non-empty "
                "condition_str. In advanced mode, write a Python boolean expression; "
                "in builder mode, condition_str must be the generated expression."
            )

            if "advanced" in cond:
                advanced = bool(cond.get("advanced"))
                builder_keys = ("field", "operator", "value")
                builder_values = {
                    key: str(cond.get(key, "")).strip()
                    for key in builder_keys
                    if key in cond
                }
                if advanced:
                    mixed = [key for key, value in builder_values.items() if value]
                    assert not mixed, (
                        f"ConditionNode condition '{cond_name or idx}' is advanced=true, "
                        f"so condition_str is the only condition definition. Remove "
                        f"builder field(s): {mixed}."
                    )
                else:
                    missing = [
                        key for key in builder_keys
                        if str(cond.get(key, "")).strip() == ""
                    ]
                    assert not missing, (
                        f"ConditionNode condition '{cond_name or idx}' is advanced=false, "
                        f"so builder fields field/operator/value must all be non-empty. "
                        f"Missing: {missing}."
                    )

        assert len(fallback_indices) == 1, (
            "ConditionNode conditions must contain exactly one fallback rule with "
            "condition_name='others' and condition_str='others'."
        )
        assert fallback_indices[0] == len(conditions) - 1, (
            "ConditionNode fallback rule must be the final item in conditions."
        )

        children = node_dict.get("children", [])
        cond_next_node_ids = [cond.get("next_node_id") for cond in conditions if cond.get("next_node_id")]

        set_cond_nodes = set(cond_next_node_ids)
        set_children = set(children)

        missing_in_children = set_cond_nodes - set_children
        missing_in_conditions = set_children - set_cond_nodes

        assert not missing_in_children, f"Found next_node_id(s) in conditions that are not in the children list: {missing_in_children}"
        assert not missing_in_conditions, f"Found node_id(s) in children list that are not mapped in any condition's next_node_id: {missing_in_conditions}"

    @safe_call_with_args(prefix="[ConditionNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        conditions = self.node_config["conditions"]

        fallback_condition_name = None

        for cond in conditions:
            cond_name = cond["condition_name"]
            cond_str = cond["condition_str"]

            if cond_str.strip() == "others":
                fallback_condition_name = cond_name
                continue

            eval_str = cond_str
            for field_name, field_value in inputs.items():
                placeholder = f"{{{field_name}}}"

                if placeholder in eval_str:
                    replace_val = repr(field_value)
                    eval_str = eval_str.replace(placeholder, replace_val)

            try:
                result = self.sandbox.evaluate(eval_str)
            except Exception as e:
                raise RuntimeError(f"Error evaluating condition '{cond_name}' (Formatted expression: {eval_str}): {str(e)}")

            if result:
                return {"condition": cond_name}

        if fallback_condition_name is not None:
            return {"condition": fallback_condition_name}

        raise ValueError("No conditions were met, and no 'others' fallback condition was found.")

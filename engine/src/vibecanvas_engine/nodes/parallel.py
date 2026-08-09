# -*- coding: utf-8 -*-
"""ParallelStartNode + ParallelEndNode — paired parallel execution boundaries."""

from copy import deepcopy

import jsonschema

from ..utils import safe_call_with_args
from ..register import node_registry
from .base import BaseNode


@node_registry.register()
class ParallelStartNode(BaseNode):
    """ParallelStartNode — splits execution into multiple concurrent branches; must be paired with a ParallelEndNode via ``parallel_end_node_id``.

    Authoring constraints (branches/pairing, empty fields, branches==children) live in ``AGENT_SPEC``.
    """
    CONFIG_SCHEMA = {
        "type": "object",
        "required": [
            "branches",
            "parallel_end_node_id"
        ],
        "properties": {
            "branches": {
                "type": "object",
                "description": "Branch map keyed by branch name. Each branch points to its first child node.",
                "additionalProperties": {
                    "type": "object",
                    "required": [
                        "next_node_id"
                    ],
                    "properties": {
                        "branch_description": {
                            "type": "string",
                            "description": "(Optional) A human-readable label for this branch; not used at runtime."
                        },
                        "next_node_id": {
                            "type": "string",
                            "description": "The first node ID of this branch; use an empty string while the branch head is not created yet."
                        }
                    },
                    "additionalProperties": False
                }
            },
            "parallel_end_node_id": {
                "type": ["string", "null"],
                "description": "The paired ParallelEndNode id; may be null while the pair is being drafted."
            }
        },
        "additionalProperties": False
    }

    AGENT_SPEC = {
        "summary": "Fan out execution into multiple independent branches that run in parallel.",
        "when_to_use": "Use when several independent analyses, API calls, or transformations can run at the same time and then rejoin.",
        "when_not_to_use": "For sequential processing use regular node chaining. For conditional branching use ConditionNode.",
        "constraints": [
            "Always pair it with one ParallelEndNode: parallel_end_node_id points to the join, and the join points back with parallel_start_node_id.",
            "branches is a map from branch name to {next_node_id, optional branch_description}; every non-empty next_node_id must appear in children, and every child must be referenced by one branch.",
            "Each branch path starts from its next_node_id and must eventually connect to the paired ParallelEndNode.",
            "When drafting, create the pair first, use parallel_end_node_id=null or branch next_node_id='' only while targets are missing, then fill both pointers after the target nodes exist."
        ],
        "config_guide": {
            "branches": "Object keyed by branch name. Each value is {next_node_id: string, branch_description?: string}.",
            "parallel_end_node_id": "The paired ParallelEndNode id."
        },
        "examples": [
            {
                "scenario": "Two completed branch heads wired to a paired join node",
                "node_dict": {
                    "node_id": "node_3",
                    "node_name": "parallel_split",
                    "node_type": "ParallelStartNode",
                    "node_description": "Split into sentiment and topic analysis",
                    "input_fields": {},
                    "output_fields": {},
                    "node_config": {
                        "branches": {
                            "sentiment": {"branch_description": "Sentiment analysis branch", "next_node_id": "node_4"},
                            "topic": {"branch_description": "Topic detection branch", "next_node_id": "node_5"}
                        },
                        "parallel_end_node_id": "node_6"
                    },
                    "children": ["node_4", "node_5"],
                    "__attributes__": {"x": 200, "y": 0}
                }
            }
        ],
        "display": {
            "name": {"en": "ParallelStartNode", "zh": "并行开始节点"},
            "description": {"en": "Split execution into concurrent branches", "zh": "将执行流拆分为并行分支"},
            "icon": "parallel_start",
            "category": {"en": "Flow Control", "zh": "流程控制"},
        }
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    @safe_call_with_args(prefix="[ParallelStartNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(
            instance=node_dict,
            schema=BaseNode.GENERAL_NODE_SCHEMA
        )

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = ParallelStartNode.CONFIG_SCHEMA
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
                        "const": "ParallelStartNode"
                    }
                }
            }
        )

        assert len(node_dict["input_fields"]) == 0, "For ParallelStartNode, the input_fields must be empty."
        assert len(node_dict["output_fields"]) == 0, "For ParallelStartNode, the output_fields must be empty."

        branches = node_dict["node_config"].get("branches", {})
        children = node_dict.get("children", [])

        branch_next_node_ids = [branch_info.get("next_node_id") for branch_info in branches.values() if branch_info.get("next_node_id")]

        set_branch_nodes = set(branch_next_node_ids)
        set_children = set(children)

        missing_in_children = set_branch_nodes - set_children
        missing_in_branches = set_children - set_branch_nodes

        assert not missing_in_children, f"Found next_node_id(s) in branches that are not in the children list: {missing_in_children}"
        assert not missing_in_branches, f"Found node_id(s) in children list that are not mapped in any branch's next_node_id: {missing_in_branches}"

        assert len(branch_next_node_ids) == len(children), "The number of next_node_ids in branches does not match the number of children. Please check for duplicates."

    @safe_call_with_args(prefix="[ParallelStartNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        return dict()


@node_registry.register()
class ParallelEndNode(BaseNode):
    """ParallelEndNode — waits for all branches of its paired ParallelStartNode to finish, then continues; linked back via ``parallel_start_node_id``.

    Authoring constraints (pairing, empty fields, single child) live in ``AGENT_SPEC``.
    """
    CONFIG_SCHEMA = {
        "type": "object",
        "required": [
            "parallel_start_node_id"
        ],
        "properties": {
            "parallel_start_node_id": {
                "type": ["string", "null"],
                "description": "The paired ParallelStartNode id; may be null while the pair is being drafted."
            }
        },
        "additionalProperties": False
    }

    AGENT_SPEC = {
        "summary": "Fan-in join that waits for all branches from a paired ParallelStartNode before continuing.",
        "when_to_use": "Use as the required join point for a ParallelStartNode block.",
        "when_not_to_use": "Do not use standalone. For ordinary sequential flow, connect nodes directly.",
        "constraints": [
            "parallel_start_node_id must point back to the paired ParallelStartNode; it may be null only while drafting.",
            "Every branch path inside the parallel block must eventually point to this node.",
            "Its optional child is the single node that runs after all parallel branches complete."
        ],
        "config_guide": {
            "parallel_start_node_id": "The paired ParallelStartNode id."
        },
        "examples": [
            {
                "scenario": "Merge point for parallel branches",
                "node_dict": {
                    "node_id": "node_6",
                    "node_name": "parallel_merge",
                    "node_type": "ParallelEndNode",
                    "node_description": "Synchronize parallel branches",
                    "input_fields": {},
                    "output_fields": {},
                    "node_config": {"parallel_start_node_id": "node_3"},
                    "children": ["node_7"],
                    "__attributes__": {"x": 600, "y": 0}
                }
            }
        ],
        "display": {
            "name": {"en": "ParallelEndNode", "zh": "并行结束节点"},
            "description": {"en": "Synchronize and merge parallel branches", "zh": "同步并合并并行分支"},
            "icon": "parallel_end",
            "category": {"en": "Flow Control", "zh": "流程控制"},
        }
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    @safe_call_with_args(prefix="[ParallelEndNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(
            instance=node_dict,
            schema=BaseNode.GENERAL_NODE_SCHEMA
        )

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = ParallelEndNode.CONFIG_SCHEMA
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
                        "const": "ParallelEndNode"
                    },
                    "children": {
                        "type": "array",
                        "maxItems": 1
                    }
                }
            }
        )

        assert len(node_dict["input_fields"]) == 0, "For ParallelEndNode, the input_fields must be empty."
        assert len(node_dict["output_fields"]) == 0, "For ParallelEndNode, the output_fields must be empty."

    @safe_call_with_args(prefix="[ParallelEndNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        return dict()

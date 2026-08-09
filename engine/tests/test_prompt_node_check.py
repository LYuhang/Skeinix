from __future__ import annotations

import copy

from vibecanvas_engine.nodes.prompt import PromptNode


def _prompt_node() -> dict:
    return {
        "node_id": "node_2",
        "node_name": "classify_text",
        "node_type": "PromptNode",
        "node_description": "Classify input text",
        "input_fields": {
            "input_text": {
                "type": "string",
                "value": "",
                "reference": "__start__.input_text",
            },
        },
        "output_fields": {
            "label": {
                "type": "string",
                "description": "Classification label",
            },
        },
        "node_config": {
            "prompt_template": (
                "# Task\nClassify {{input_text}}.\n\n"
                "# Output Format\n```json\n{\"label\": \"[label]\"}\n```"
            ),
            "model_name": "test-model",
            "inference_config": {
                "temperature": 0,
                "max_tokens": 64,
                "top_k": -1,
                "top_p": 1,
            },
        },
        "children": [],
        "__attributes__": {"x": 200, "y": 0},
    }


def test_prompt_node_check_accepts_primitive_input_fields():
    result = PromptNode.check(_prompt_node())
    assert result["status"] == "success"


def test_prompt_node_check_rejects_nested_input_fields():
    node = copy.deepcopy(_prompt_node())
    node["input_fields"]["items"] = {
        "type": "array",
        "value": [],
        "reference": "__start__.items",
        "schema": {"type": "array", "items": {"type": "string"}},
    }

    result = PromptNode.check(node)

    assert result["status"] == "error"
    assert "PromptNode input field 'items' has nested type 'array'" in result["error_message"]
    assert "CodeNode" in result["error_message"]
    assert "prompt-ready string" in result["error_message"]

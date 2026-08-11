"""PromptNode rejects empty or structurally invalid model responses."""

from __future__ import annotations

from vibecanvas_engine.nodes.prompt import PromptNode


class _StaticModel:
    def __init__(self, output: str):
        self.output = output

    def __call__(self, conversation_dict, inference_config=None, stop_event=None):
        return self.output


def _call(monkeypatch, output: str):
    node = PromptNode.__new__(PromptNode)
    node.node_config = {
        "prompt_template": "Return JSON.",
        "model_name": "saved-model",
        "inference_config": {},
    }
    node.output_fields = {
        "headline": {"type": "string", "description": "Insight headline"},
        "recommendation": {
            "type": "string",
            "description": "Recommended action",
        },
    }
    monkeypatch.setattr(
        PromptNode,
        "_build_injected_model",
        staticmethod(lambda _entry: _StaticModel(output)),
    )
    return node(
        inputs={},
        previous_outputs={},
        extra={"llm_credentials": {"saved-model": {}}},
    )


def test_empty_model_text_is_an_error(monkeypatch):
    result = _call(monkeypatch, "")

    assert result["status"] == "error"
    assert "model returned no text" in result["error_message"]


def test_non_object_json_is_an_error(monkeypatch):
    result = _call(monkeypatch, '["not", "an", "object"]')

    assert result["status"] == "error"
    assert "must be a JSON object" in result["error_message"]


def test_missing_declared_field_is_an_error(monkeypatch):
    result = _call(monkeypatch, '{"headline": "One insight"}')

    assert result["status"] == "error"
    assert "recommendation" in result["error_message"]


def test_complete_object_is_returned(monkeypatch):
    result = _call(
        monkeypatch,
        '{"headline": "One insight", "recommendation": "Take action"}',
    )

    assert result["status"] == "success"
    assert result["output"] == {
        "headline": "One insight",
        "recommendation": "Take action",
    }

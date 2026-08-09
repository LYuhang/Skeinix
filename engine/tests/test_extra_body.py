"""PromptNode extra_body parsing (inference_config['extra_body'])."""
from vibecanvas_engine.custom_llms import _parse_extra_body


def test_parse_extra_body_json_string():
    assert _parse_extra_body({"extra_body": '{"reasoning_effort":"high"}'}) == {
        "reasoning_effort": "high"
    }


def test_parse_extra_body_dict_passthrough():
    assert _parse_extra_body({"extra_body": {"a": 1}}) == {"a": 1}


def test_parse_extra_body_absent_or_blank():
    assert _parse_extra_body({}) is None
    assert _parse_extra_body({"extra_body": ""}) is None
    assert _parse_extra_body({"extra_body": None}) is None


def test_parse_extra_body_malformed_is_ignored():
    # bad JSON → None (never raises); valid JSON that isn't an object → None
    assert _parse_extra_body({"extra_body": "{not json"}) is None
    assert _parse_extra_body({"extra_body": "[1, 2]"}) is None

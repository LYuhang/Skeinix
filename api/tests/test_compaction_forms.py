import json
from vibecanvas_api.agents.middleware.compaction_forms import (
    parse_envelope, output_content_type, output_path, render_aged,
)

ENV = json.dumps({
    "status": "success", "error": None, "abstract": "Filtered 12 of 340",
    "output": {"path": "/data/query_1.jsonl", "content_type": "table/jsonl",
               "data": [{"a": 1}]},
}, ensure_ascii=False)


def test_parse_and_accessors():
    env = parse_envelope(ENV)
    assert env is not None
    assert output_content_type(env) == "table/jsonl"
    assert output_path(env) == "/data/query_1.jsonl"
    assert parse_envelope("not json") is None
    assert parse_envelope('{"x":1}') is None


def test_reference_drops_data_keeps_abstract_path_and_is_deterministic():
    env = parse_envelope(ENV)
    a = render_aged(env, ENV, "reference")
    b = render_aged(env, ENV, "reference")
    assert a == b
    obj = json.loads(a)
    assert "data" not in obj["output"]
    assert obj["output"]["path"] == "/data/query_1.jsonl"
    assert obj["abstract"] == "Filtered 12 of 340"


def test_reference_prefers_llm_abstract():
    env = parse_envelope(ENV); env["llm_abstract"] = "rich summary"
    assert json.loads(render_aged(env, ENV, "reference"))["abstract"] == "rich summary"


def test_minimal_is_a_fixed_path_stub():
    env = parse_envelope(ENV)
    assert render_aged(env, ENV, "minimal") == "[output elided: /data/query_1.jsonl]"


def test_head_tail_keeps_head_and_tail():
    body = "\n".join(f"line {i}" for i in range(100))
    env = {"status": "success", "output": {"path": "/exec/cmd_1.log",
            "content_type": "text/shell", "data": body}}
    out = render_aged(env, json.dumps(env), "head_tail")
    assert "line 0" in out and "line 99" in out and "elided" in out
    assert len(out) < len(body)


def test_non_envelope_short_content_is_kept_whole_not_blanket_elided():
    # env=None (non-envelope tool return) must NOT collapse to "[output elided]" —
    # short content (under head+tail lines) is returned whole; only huge blobs trim.
    err = '{"status":"error","error":"permission denied"}'
    assert render_aged(None, err, "reference") == err
    assert render_aged(None, err, "minimal") == err
    huge = "\n".join(f"row {i}" for i in range(500))
    trimmed = render_aged(None, huge, "reference")
    assert "row 0" in trimmed and "row 499" in trimmed and "elided" in trimmed
    assert len(trimmed) < len(huge)

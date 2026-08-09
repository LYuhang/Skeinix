import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.token_accounting import (
    build_message_tokens,
    count_tokens,
    estimate_context_tokens,
    record_message_tokens,
    message_tokens,
)


def _env(path="/data/a.jsonl", ct="table/jsonl", data="x" * 4000, abstract="short abstract"):
    return json.dumps(
        {"status": "success", "error": None, "abstract": abstract,
         "output": {"path": path, "content_type": ct, "data": data}},
        ensure_ascii=False,
    )


# ---------------------------------------------------------------- count_tokens

def test_count_tokens_fallback_is_chars_over_4():
    # An unknown / unavailable model must fall back to count_tokens_approximately
    # (chars≈4). For a plain ascii string the approximate count is len//4-ish.
    text = "x" * 400
    n = count_tokens(text, "nonexistent-provider:made-up-model")
    assert isinstance(n, int)
    assert 80 <= n <= 120  # ~100, the chars/4 ballpark


def test_count_tokens_empty_is_zero_or_small():
    assert count_tokens("", "anything") >= 0


def test_count_tokens_monotonic():
    short = count_tokens("a" * 40, "m")
    long = count_tokens("a" * 4000, "m")
    assert long > short


# --------------------------------------------------------- build_message_tokens

def test_build_tokens_for_tool_envelope_raw_gt_abstract():
    content = _env()
    tok = build_message_tokens(content, model="m", form="raw")
    assert tok["raw"] > 0
    assert tok["abstract"] is not None
    assert tok["raw"] > tok["abstract"]  # full content tokenizes larger than the abstract
    assert tok["compressed"] is None
    assert tok["form"] == "raw"
    assert tok["model"] == "m"


def test_build_tokens_for_plain_message_has_no_abstract():
    tok = build_message_tokens("just a normal human turn", model="m", form="raw")
    assert tok["raw"] > 0
    assert tok["abstract"] is None
    assert tok["head_tail"] is None
    assert tok["compressed"] is None
    assert tok["form"] == "raw"


def test_build_tokens_failsoft_on_malformed_envelope():
    # A string that looks like JSON but is not a valid envelope must not raise;
    # treated as a plain message (raw only).
    tok = build_message_tokens("{not valid json", model="m", form="raw")
    assert tok["raw"] >= 0
    assert tok["abstract"] is None


def test_build_tokens_non_envelope_json_object():
    # A JSON object lacking status/output keys is not an envelope -> plain.
    tok = build_message_tokens(json.dumps({"foo": "bar"}), model="m", form="raw")
    assert tok["abstract"] is None


def test_build_tokens_records_head_tail_for_file_tool_artifact():
    artifact = {
        "schema_version": 1,
        "status": "success",
        "artifact": {"kind": "tool_result", "target": {"path": "/data/a.txt"}},
        "payload": {"kind": "inline", "ref": "/data/a.txt"},
        "meta": {"tool": "read_file", "content_type": "text/plain"},
        "content_abstract": "Read /data/a.txt",
    }
    tok = build_message_tokens("line\n" * 5000, model="m", form="raw", artifact=artifact)
    assert tok["raw"] > 0
    assert tok["abstract"] is not None
    assert tok["head_tail"] is not None
    assert tok["abstract"] < tok["head_tail"] < tok["raw"]


def test_build_tokens_leaves_head_tail_empty_for_non_file_tool_artifact():
    artifact = {
        "schema_version": 1,
        "status": "success",
        "artifact": {"kind": "tool_result"},
        "meta": {"tool": "inspect_data", "content_type": "application/json"},
        "content_abstract": "inspected data",
    }
    tok = build_message_tokens("x" * 4000, model="m", form="raw", artifact=artifact)
    assert tok["abstract"] is not None
    assert tok["head_tail"] is None


# ------------------------------------------------------- estimate_context_tokens

def test_estimate_sums_current_form_respecting_recorded_tokens():
    h = HumanMessage(content="hello")
    record_message_tokens(h, model="m")
    a = AIMessage(content="hi there, here is a longer answer")
    record_message_tokens(a, model="m")
    t = ToolMessage(content=_env(), tool_call_id="c", name="t")
    record_message_tokens(t, model="m", form="raw")

    total = estimate_context_tokens([h, a, t])
    expected = (message_tokens(h)["raw"]
                + message_tokens(a)["raw"]
                + message_tokens(t)["raw"])
    assert total == expected


def test_estimate_respects_degraded_form():
    t = ToolMessage(content=_env(), tool_call_id="c", name="t")
    # record both raw and abstract sizes, but mark current form = abstract
    record_message_tokens(t, model="m", form="raw")
    raw_total = estimate_context_tokens([t])

    # Now simulate S0/S1 degradation: same message, current form = abstract.
    t2 = ToolMessage(content=_env(), tool_call_id="c", name="t")
    record_message_tokens(t2, model="m", form="abstract")
    abstract_total = estimate_context_tokens([t2])

    assert abstract_total < raw_total  # the degraded form is cheaper


def test_estimate_uses_recorded_head_tail_for_file_tool():
    artifact = {
        "schema_version": 1,
        "status": "success",
        "artifact": {"kind": "tool_result", "target": {"path": "/data/a.txt"}},
        "payload": {"kind": "inline", "ref": "/data/a.txt"},
        "meta": {"tool": "read_file", "content_type": "text/plain"},
        "content_abstract": "Read /data/a.txt",
    }
    t = ToolMessage(content="line\n" * 5000, tool_call_id="c", name="read_file", artifact=artifact)
    record_message_tokens(t, model="m", form="raw")
    tok = message_tokens(t)
    raw_total = estimate_context_tokens([t])

    t.response_metadata["tokens"] = {**tok, "form": "head_tail"}
    head_tail_total = estimate_context_tokens([t])

    t.response_metadata["tokens"] = {**tok, "form": "abstract"}
    abstract_total = estimate_context_tokens([t])

    assert abstract_total < head_tail_total < raw_total
    assert head_tail_total == tok["head_tail"]


def test_estimate_falls_back_to_counting_unrecorded_message():
    # A message with no meta.tokens recorded -> count its current content.
    h = HumanMessage(content="x" * 400)
    total = estimate_context_tokens([h])
    assert total > 0


def test_estimate_failsoft_on_garbage():
    class Weird:
        content = None
    # Must not raise on a message without usable content / metadata.
    assert estimate_context_tokens([Weird()]) >= 0


# ------------------------------------------------------------- record / message

def test_record_attaches_to_response_metadata_for_ai_and_tool():
    a = AIMessage(content="answer")
    record_message_tokens(a, model="m")
    assert a.response_metadata["tokens"]["form"] == "raw"

    t = ToolMessage(content=_env(), tool_call_id="c", name="t")
    record_message_tokens(t, model="m")
    assert t.response_metadata["tokens"]["raw"] > 0


def test_record_attaches_to_additional_kwargs_for_human():
    h = HumanMessage(content="hi")
    record_message_tokens(h, model="m")
    assert h.additional_kwargs["tokens"]["form"] == "raw"
    # message_tokens reads from the right place transparently
    assert message_tokens(h) == h.additional_kwargs["tokens"]


def test_record_is_idempotent_no_drift():
    t = ToolMessage(content=_env(), tool_call_id="c", name="t")
    record_message_tokens(t, model="m", form="raw")
    first = dict(t.response_metadata["tokens"])
    record_message_tokens(t, model="m", form="raw")
    assert t.response_metadata["tokens"] == first  # re-record same form = no drift


def test_record_refreshes_form_on_degrade():
    t = ToolMessage(content=_env(), tool_call_id="c", name="t")
    record_message_tokens(t, model="m", form="raw")
    record_message_tokens(t, model="m", form="abstract")
    assert t.response_metadata["tokens"]["form"] == "abstract"
    # raw is preserved across the form change
    assert t.response_metadata["tokens"]["raw"] > 0

"""S2a context-aware per-output LLM compaction.

All unit-testable WITHOUT a live LLM: the LLM call is behind an injected
``summarize_fn``; the persistent cache is an in-memory fake keyed by
``tool_call_id``.
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.middleware.s2a_compaction import (
    S2aCompactor,
    build_summary_prompt,
    envelope_body,
    find_paired_call_args,
    is_oversize,
    latest_human_intent,
)
from vibecanvas_api.agents.middleware.compaction_forms import parse_envelope
from vibecanvas_api.agents.token_accounting import record_message_tokens, message_tokens


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _env(path, ct, data="x", abstract="cheap abstract", llm_abstract=None):
    out = {"path": path, "content_type": ct, "data": data}
    env = {"status": "success", "error": None, "abstract": abstract, "output": out}
    if llm_abstract is not None:
        env["llm_abstract"] = llm_abstract
    return json.dumps(env, ensure_ascii=False)


def _tool(content, tool_call_id="tc1", name="read_file"):
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name)


def _stamp_raw_tokens(msg, raw):
    """Force meta.tokens.raw on a message (so is_oversize reads it)."""
    record_message_tokens(msg, model="m", form="raw")
    tok = message_tokens(msg)
    tok["raw"] = raw


class _FakeCache:
    """In-memory stand-in for the VFS cache, keyed by tool_call_id."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.reads = 0
        self.writes = 0

    def read(self, tool_call_id):
        self.reads += 1
        return self.store.get(tool_call_id)

    def write(self, tool_call_id, text):
        self.writes += 1
        self.store[tool_call_id] = text


# --------------------------------------------------------------------------- #
# is_oversize
# --------------------------------------------------------------------------- #

def test_is_oversize_over_cap_text():
    msg = _tool(_env("/exec/big.log", "text/plain", data="y" * 100))
    _stamp_raw_tokens(msg, 9000)
    assert is_oversize(msg, cap=8000) is True


def test_is_oversize_under_cap():
    msg = _tool(_env("/exec/small.log", "text/plain"))
    _stamp_raw_tokens(msg, 100)
    assert is_oversize(msg, cap=8000) is False


def test_is_oversize_gates_on_content_type():
    # link/cloud_table is tiny (a URL) — never S2a even if it claimed big tokens.
    msg = _tool(_env("https://x", "link/cloud_table"))
    _stamp_raw_tokens(msg, 50000)
    assert is_oversize(msg, cap=8000) is False


def test_is_oversize_applies_to_each_text_log_type():
    for ct in ("text/plain", "text/markdown", "text/html", "text/shell", "application/json"):
        msg = _tool(_env("/exec/x", ct))
        _stamp_raw_tokens(msg, 20000)
        assert is_oversize(msg, cap=8000) is True, ct


def test_is_oversize_no_tokens_meta_falls_back_to_counting():
    # No recorded meta.tokens → counts current content. A small body is under cap.
    msg = _tool(_env("/exec/x", "text/plain"))
    assert is_oversize(msg, cap=8000) is False


def test_is_oversize_non_envelope_is_false():
    msg = _tool("just a string, not an envelope")
    _stamp_raw_tokens(msg, 99999)
    assert is_oversize(msg, cap=8000) is False


# --------------------------------------------------------------------------- #
# context extraction + prompt builder (pure)
# --------------------------------------------------------------------------- #

def test_find_paired_call_args():
    ai = AIMessage(content="", tool_calls=[
        {"id": "tc1", "name": "read_file", "args": {"path": "/exec/big.log"}},
        {"id": "tc2", "name": "inspect_data", "args": {"path": "/data/sample.jsonl"}},
    ])
    msgs = [HumanMessage(content="hi"), ai, _tool(_env("/exec/big.log", "text/plain"))]
    assert find_paired_call_args(msgs, "tc1") == {"path": "/exec/big.log"}
    assert find_paired_call_args(msgs, "tc2") == {"path": "/data/sample.jsonl"}
    assert find_paired_call_args(msgs, "missing") == {}


def test_latest_human_intent():
    msgs = [
        HumanMessage(content="first task"),
        AIMessage(content="ok"),
        HumanMessage(content="the active intent"),
        AIMessage(content="working"),
    ]
    assert latest_human_intent(msgs) == "the active intent"


def test_build_summary_prompt_includes_args_intent_and_paths():
    tool_msg = _tool(_env("/exec/big.log", "text/plain", data="lots of log lines\n/data/ref.jsonl"))
    prompt = build_summary_prompt(
        tool_msg, tool_args={"path": "/exec/big.log"}, intent="find the error", tool_name="read_file"
    )
    assert "read_file" in prompt
    assert "/exec/big.log" in prompt           # call args present
    assert "find the error" in prompt          # intent present
    assert "path" in prompt.lower()            # instructs to preserve paths/refs
    assert "/data/ref.jsonl" in prompt         # body present so refs can be preserved


# --------------------------------------------------------------------------- #
# the compaction step
# --------------------------------------------------------------------------- #

def _oversize_msgs(tool_call_id="tc1"):
    ai = AIMessage(content="", tool_calls=[
        {"id": tool_call_id, "name": "read_file", "args": {"path": "/exec/big.log"}}])
    tool = _tool(_env("/exec/big.log", "text/plain", data="z" * 200), tool_call_id=tool_call_id)
    _stamp_raw_tokens(tool, 20000)
    return [HumanMessage(content="summarize the failure"), ai, tool]


def test_miss_computes_caches_and_fills_llm_abstract():
    cache = _FakeCache()
    calls = []

    def fake_summarize(prompt):
        calls.append(prompt)
        return "GIST: the run failed at step 3"

    msgs = _oversize_msgs()
    S2aCompactor(summarize_fn=fake_summarize, cache=cache, cap=8000, model="m").apply(msgs)

    env = parse_envelope(msgs[2].content)
    assert env["llm_abstract"] == "GIST: the run failed at step 3"
    assert env["abstract"] == "cheap abstract"          # original deterministic abstract kept
    assert env["output"]["data"] == "z" * 200           # full body still inline (S2a is gist, not elision)
    assert cache.writes == 1 and cache.store["tc1"] == "GIST: the run failed at step 3"
    assert len(calls) == 1                               # summarize_fn invoked once
    # meta.tokens.compressed recorded + form stamped
    tok = message_tokens(msgs[2])
    assert isinstance(tok["compressed"], int) and tok["compressed"] > 0


def test_hit_does_not_recompute_frozen_once():
    cache = _FakeCache()
    cache.store["tc1"] = "CACHED GIST"
    calls = []

    def fake_summarize(prompt):
        calls.append(prompt)
        return "SHOULD NOT BE CALLED"

    msgs = _oversize_msgs()
    S2aCompactor(summarize_fn=fake_summarize, cache=cache, cap=8000, model="m").apply(msgs)

    env = parse_envelope(msgs[2].content)
    assert env["llm_abstract"] == "CACHED GIST"
    assert calls == []                                   # frozen-once: no recompute on hit
    assert cache.writes == 0


def test_under_cap_is_skipped():
    cache = _FakeCache()
    ai = AIMessage(content="", tool_calls=[
        {"id": "tc1", "name": "read_file", "args": {"path": "/x"}}])
    tool = _tool(_env("/x", "text/plain"), tool_call_id="tc1")
    _stamp_raw_tokens(tool, 100)
    msgs = [HumanMessage(content="go"), ai, tool]
    S2aCompactor(summarize_fn=lambda p: "X", cache=cache, cap=8000, model="m").apply(msgs)
    assert parse_envelope(msgs[2].content).get("llm_abstract") is None
    assert cache.writes == 0


def test_fail_soft_summarize_raises_keeps_deterministic_abstract():
    cache = _FakeCache()

    def boom(prompt):
        raise RuntimeError("LLM down")

    msgs = _oversize_msgs()
    S2aCompactor(summarize_fn=boom, cache=cache, cap=8000, model="m").apply(msgs)
    # No llm_abstract filled; content unchanged (deterministic abstract survives for S1).
    env = parse_envelope(msgs[2].content)
    assert env.get("llm_abstract") is None
    assert env["abstract"] == "cheap abstract"
    assert cache.writes == 0


def test_no_summarize_fn_or_no_cache_is_inert():
    msgs = _oversize_msgs()
    before = msgs[2].content
    S2aCompactor(summarize_fn=None, cache=_FakeCache(), cap=8000, model="m").apply(msgs)
    assert msgs[2].content == before
    msgs2 = _oversize_msgs()
    before2 = msgs2[2].content
    S2aCompactor(summarize_fn=lambda p: "x", cache=None, cap=8000, model="m").apply(msgs2)
    assert msgs2[2].content == before2


# --------------------------------------------------------------------------- #
# the reference stub prefers llm_abstract once present
# --------------------------------------------------------------------------- #

def test_reference_stub_prefers_llm_abstract():
    from vibecanvas_api.agents.middleware.compaction_forms import render_aged
    env_str = _env("/exec/big.log", "text/plain", llm_abstract="LLM GIST", abstract="cheap")
    env = parse_envelope(env_str)
    stub = render_aged(env, env_str, "reference")
    obj = json.loads(stub)
    assert obj["abstract"] == "LLM GIST"     # llm_abstract wins over cheap abstract


def test_reference_stub_falls_back_to_abstract_when_no_llm_abstract():
    from vibecanvas_api.agents.middleware.compaction_forms import render_aged
    env_str = _env("/exec/big.log", "text/plain", abstract="cheap")
    env = parse_envelope(env_str)
    stub = render_aged(env, env_str, "reference")
    obj = json.loads(stub)
    assert obj["abstract"] == "cheap"


# --------------------------------------------------------------------------- #
# §4.1a fix: S2a reads the FULL body from VFS by path, not the omitted data
# --------------------------------------------------------------------------- #

class _FakeReader:
    def __init__(self, store=None):
        self.store = dict(store or {})
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        return self.store.get(path)


def _omitted_env(path, ct="text/plain", abstract="cheap", full_tokens=20000):
    # Large output: inline data omitted, full size recorded — like fill_output_data.
    out = {"path": path, "content_type": ct, "full_tokens": full_tokens}
    return json.dumps({"status": "success", "error": None, "abstract": abstract,
                       "output": out}, ensure_ascii=False)


def test_envelope_body_prefers_vfs_full_over_inline():
    full = "the FULL original body from VFS\n/data/ref.jsonl"
    reader = _FakeReader({"/exec/big.log": full})
    env = parse_envelope(_env("/exec/big.log", "text/plain", data="inline short"))
    assert envelope_body(env, vfs_reader=reader) == full   # VFS full wins
    assert reader.reads == ["/exec/big.log"]


def test_envelope_body_falls_back_to_inline_then_abstract():
    env = parse_envelope(_env("/x", "text/plain", data="inline body"))
    assert envelope_body(env, vfs_reader=_FakeReader({})) == "inline body"  # VFS miss → inline
    env2 = parse_envelope(_omitted_env("/gone"))
    # no inline data + VFS miss → the cheap abstract
    assert envelope_body(env2, vfs_reader=_FakeReader({})) == "cheap"


def test_s2a_gist_summarizes_full_vfs_body():
    full = "FULL BODY giant log with the error at /data/ref.jsonl"
    reader = _FakeReader({"/exec/big.log": full})
    cache = _FakeCache()
    seen = []

    def fake_summarize(prompt):
        seen.append(prompt)
        return "GIST from full"

    ai = AIMessage(content="", tool_calls=[
        {"id": "tc1", "name": "read_file", "args": {"path": "/exec/big.log"}}])
    tool = _tool(_omitted_env("/exec/big.log"), tool_call_id="tc1")
    msgs = [HumanMessage(content="find the error"), ai, tool]
    S2aCompactor(summarize_fn=fake_summarize, cache=cache, cap=8000, model="m",
                 vfs_reader=reader).apply(msgs)

    env = parse_envelope(msgs[2].content)
    assert env["llm_abstract"] == "GIST from full"
    # the prompt summarized the FULL VFS body (the bug fix), not the omitted body
    assert "FULL BODY giant log" in seen[0]
    assert "/data/ref.jsonl" in seen[0]
    assert reader.reads == ["/exec/big.log"]


def test_s2a_oversize_uses_full_tokens_when_no_raw_meta():
    # No meta.tokens recorded, body inline omitted → is_oversize reads full_tokens.
    msg = _tool(_omitted_env("/exec/big.log", full_tokens=20000))
    assert is_oversize(msg, cap=8000) is True
    msg2 = _tool(_omitted_env("/exec/small.log", full_tokens=100))
    assert is_oversize(msg2, cap=8000) is False


def test_s2a_failsoft_when_path_missing():
    # VFS reader present but path not stored → envelope_body falls back to abstract;
    # the gist still computes (over the abstract) — never raises.
    reader = _FakeReader({})
    cache = _FakeCache()
    ai = AIMessage(content="", tool_calls=[
        {"id": "tc1", "name": "read_file", "args": {"path": "/exec/gone.log"}}])
    tool = _tool(_omitted_env("/exec/gone.log"), tool_call_id="tc1")
    msgs = [HumanMessage(content="x"), ai, tool]
    S2aCompactor(summarize_fn=lambda p: "G", cache=cache, cap=8000, model="m",
                 vfs_reader=reader).apply(msgs)
    # did not crash; llm_abstract filled from the abstract-fallback body
    assert parse_envelope(msgs[2].content)["llm_abstract"] == "G"

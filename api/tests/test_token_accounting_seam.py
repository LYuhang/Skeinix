"""Integration: meta.tokens attach seam (LifecyclePolicyEdit) + config slot."""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.token_accounting import (
    estimate_context_tokens, message_tokens,
)
from vibecanvas_api.config import AgentConfig


def _env(path, ct="table/jsonl", data="x" * 6000, abstract="short"):
    return json.dumps(
        {"status": "success", "error": None, "abstract": abstract,
         "output": {"path": path, "content_type": ct, "data": data}},
        ensure_ascii=False,
    )


def _tool(content):
    return ToolMessage(content=content, tool_call_id="c", name="t")


def _toklen(messages):
    return sum(len(getattr(m, "content", "") or "") for m in messages) // 4


# ----------------------------------------------------------------- attach seam

def test_seam_records_tokens_on_every_message_with_form():
    msgs = [
        HumanMessage(content="build me a workflow"),
        AIMessage(content="ok, on it"),
        _tool(_env("/data/a.jsonl")),
    ]
    LifecyclePolicyEdit(trigger=10_000_000, clear_at_least=0, model="m").apply(
        msgs, count_tokens=_toklen)
    for m in msgs:
        tok = message_tokens(m)
        assert tok is not None
        assert tok["raw"] > 0
        assert tok["form"] == "raw"  # below trigger -> nothing degraded
        assert tok["model"] == "m"
    # the tool envelope got an abstract count; plain messages did not
    assert message_tokens(msgs[2])["abstract"] is not None
    assert message_tokens(msgs[0])["abstract"] is None


def test_seam_degraded_messages_record_degraded_form():
    # 6 large tool outputs; fresh_k=3 stay raw, the 3 oldest degrade to reference.
    msgs = [_tool(_env(f"/data/q{i}.jsonl")) for i in range(6)]
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0, model="m").apply(
        msgs, count_tokens=_toklen)
    forms = [message_tokens(m)["form"] for m in msgs]
    assert forms[3:] == ["raw", "raw", "raw"]
    assert all(f == "reference" for f in forms[:3])


def test_seam_idempotent_no_drift():
    msgs = [_tool(_env(f"/data/q{i}.jsonl")) for i in range(6)]
    LifecyclePolicyEdit(trigger=1_000, model="m").apply(msgs, count_tokens=_toklen)
    snap = [dict(message_tokens(m)) for m in msgs]
    # re-run: already-cleared messages are skipped by compaction; recording the
    # same form over the same content must not change the recorded tokens.
    LifecyclePolicyEdit(trigger=1_000, model="m").apply(msgs, count_tokens=_toklen)
    assert [dict(message_tokens(m)) for m in msgs] == snap


def test_seam_estimate_matches_recorded_sum_after_apply():
    msgs = [_tool(_env(f"/data/q{i}.jsonl")) for i in range(6)]
    LifecyclePolicyEdit(trigger=1_000, model="m").apply(msgs, count_tokens=_toklen)
    # estimate reads each message's CURRENT-form field; degraded ones read the
    # cheaper 'abstract' count, so the total is below the all-raw sum.
    est = estimate_context_tokens(msgs, model="m")
    all_raw = sum(message_tokens(m)["raw"] for m in msgs)
    assert 0 < est <= all_raw


def test_seam_failsoft_does_not_raise_on_empty_model():
    msgs = [HumanMessage(content="hi"), _tool(_env("/data/a.jsonl"))]
    LifecyclePolicyEdit(trigger=10_000, model="").apply(msgs, count_tokens=_toklen)
    assert message_tokens(msgs[0]) is not None


# ----------------------------------------------------------------- config slot

def test_compaction_model_slot_default_falls_back_to_agent_model():
    cfg = AgentConfig({"model": "openai:gpt-4o"})
    assert cfg.compaction_model == ""
    assert cfg.resolve_compaction_model() == "openai:gpt-4o"


def test_compaction_model_slot_reads_configured_value():
    cfg = AgentConfig({"model": "openai:gpt-4o",
                       "compaction": {"model": "openai:gpt-4o-mini"}})
    assert cfg.compaction_model == "openai:gpt-4o-mini"
    assert cfg.resolve_compaction_model() == "openai:gpt-4o-mini"

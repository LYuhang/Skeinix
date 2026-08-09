# -*- coding: utf-8 -*-
"""LifecyclePolicyEdit reads pre-recorded
``meta.tokens`` instead of recomputing it every turn.

When a message already carries a ``meta.tokens`` recorded at creation time (by
``TokenRecordMiddleware``), the compaction pass must PRESERVE the original
``raw``/``abstract`` (the count of the ORIGINAL content, needed for tokens-saved
reporting) and only UPDATE ``form`` (+ ``compressed`` when S2a/S2b fill it). It
must NOT overwrite ``raw`` with the (smaller) count of the degraded stub.

If a message has NO recorded tokens (old pre-fix history), it falls back to the
compute-once behaviour (the prior P2a pass).
"""
from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.token_accounting import (
    message_tokens, record_message_tokens,
)


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


def test_preserves_pre_recorded_raw_through_degrade():
    # 6 large tool outputs, ALL pre-recorded at creation with form='raw'.
    msgs = [_tool(_env(f"/data/q{i}.jsonl")) for i in range(6)]
    for m in msgs:
        record_message_tokens(m, model="m", form="raw")
    pre_raw = [message_tokens(m)["raw"] for m in msgs]
    assert all(r > 0 for r in pre_raw)

    # trigger compaction: fresh_k=3 stay raw, the 3 oldest degrade to 'reference'.
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0, model="m").apply(
        msgs, count_tokens=_toklen)

    forms = [message_tokens(m)["form"] for m in msgs]
    assert all(f == "reference" for f in forms[:3])
    assert forms[3:] == ["raw", "raw", "raw"]

    # CRUX: the degraded messages keep their ORIGINAL raw (NOT the tiny stub count).
    for i in range(3):
        tok = message_tokens(msgs[i])
        assert tok["raw"] == pre_raw[i], (
            f"degraded msg {i} raw was overwritten: {tok['raw']} != {pre_raw[i]}")
        # abstract is also the original recorded value, unchanged
        assert tok["abstract"] is not None


def test_preserves_compressed_and_only_updates_form():
    msg = _tool(_env("/data/q0.jsonl"))
    record_message_tokens(msg, model="m", form="raw")
    # pretend S2a already filled a compressed count
    message_tokens(msg)["compressed"] = 123
    orig_raw = message_tokens(msg)["raw"]
    orig_abstract = message_tokens(msg)["abstract"]

    # Many siblings so this one is past fresh_k and degrades.
    others = [_tool(_env(f"/data/x{i}.jsonl")) for i in range(5)]
    for m in others:
        record_message_tokens(m, model="m", form="raw")
    msgs = [msg, *others]
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0, model="m").apply(
        msgs, count_tokens=_toklen)

    # _compact replaces the degraded slot with a model_copy, so read msgs[0]
    # (the object the edit stamped), not the stale pre-apply `msg` reference.
    tok = message_tokens(msgs[0])
    assert tok["form"] == "reference"      # form updated
    assert tok["raw"] == orig_raw          # raw preserved
    assert tok["abstract"] == orig_abstract
    assert tok["compressed"] == 123        # compressed preserved


def test_no_record_falls_back_to_compute_once():
    # NO pre-recording (old history) → the pass records once from current content.
    msgs = [_tool(_env(f"/data/q{i}.jsonl")) for i in range(6)]
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0, model="m").apply(
        msgs, count_tokens=_toklen)
    for m in msgs:
        tok = message_tokens(m)
        assert tok is not None and tok["raw"] > 0
    # degraded ones recorded the 'reference' form
    forms = [message_tokens(m)["form"] for m in msgs]
    assert any(f == "reference" for f in forms)


def test_below_trigger_preserves_recorded_raw_and_form_stays_raw():
    msgs = [_tool(_env(f"/data/q{i}.jsonl")) for i in range(3)]
    for m in msgs:
        record_message_tokens(m, model="m", form="raw")
    pre = [dict(message_tokens(m)) for m in msgs]
    # huge trigger → nothing degrades; recorded tokens must be untouched.
    LifecyclePolicyEdit(trigger=10_000_000, clear_at_least=0, model="m").apply(
        msgs, count_tokens=_toklen)
    assert [dict(message_tokens(m)) for m in msgs] == pre

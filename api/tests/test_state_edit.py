"""Pinned state.md core-memory injection through ``StateEdit``.

A pinned ContextEdit that re-injects the agent-curated /memory/state.md at the
context TAIL each turn (mirrors RecitationEdit): keep-latest, fail-soft, byte-
stable when unchanged, NEVER compacted (appended after compaction).
"""
import json

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from vibecanvas_api.agents.middleware.state_edit import StateEdit, STATE_HEADER


def _toklen(ms):
    return sum(len(getattr(m, "content", "") or "") for m in ms) // 4


class _FakeVfs:
    """A minimal sync VFS facade keyed by (wf_id, path) → an object with .content."""

    def __init__(self, files=None):
        self._files = dict(files or {})

    def read(self, *, wf_id, path):
        body = self._files.get((wf_id, path))
        if body is None:
            return None
        return type("E", (), {"content": body, "kind": "scratch",
                              "content_type": "text/markdown"})()

    def write_scratch(self, *, wf_id, path, content, content_type="text/plain", abstract=""):
        self._files[(wf_id, path)] = content


def test_appends_state_at_tail():
    vfs = _FakeVfs({("wf1", "/memory/state.md"): "# Goal\nBuild a workflow.\n"})
    msgs = [SystemMessage(content="sys"), HumanMessage(content="go"), AIMessage(content="ok")]
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    assert STATE_HEADER in msgs[-1].content
    assert "Build a workflow." in msgs[-1].content
    # Provider-agnostic tail placement: a HumanMessage wrapped in <system-reminder>
    # (NOT a SystemMessage, which Anthropic/Gemini adapters hoist off the tail).
    assert isinstance(msgs[-1], HumanMessage)
    assert "<system-reminder>" in msgs[-1].content
    assert len(msgs) == 4


def test_keep_latest_supersedes_prior_state():
    vfs = _FakeVfs({("wf1", "/memory/state.md"): "v1"})
    msgs = [HumanMessage(content="go")]
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    # state.md changed between turns → second apply must REPLACE, not stack
    vfs.write_scratch(wf_id="wf1", path="/memory/state.md", content="v2")
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    blocks = [m for m in msgs if STATE_HEADER in (getattr(m, "content", "") or "")]
    assert len(blocks) == 1
    assert "v2" in msgs[-1].content
    assert "v1" not in msgs[-1].content


def test_noop_when_absent():
    vfs = _FakeVfs({})  # no state.md
    msgs = [HumanMessage(content="go")]
    before = [m.content for m in msgs]
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    assert [m.content for m in msgs] == before  # nothing injected


def test_noop_when_empty():
    vfs = _FakeVfs({("wf1", "/memory/state.md"): "   \n  "})  # whitespace-only
    msgs = [HumanMessage(content="go")]
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    assert all(STATE_HEADER not in (getattr(m, "content", "") or "") for m in msgs)


def test_byte_stable_when_unchanged():
    vfs = _FakeVfs({("wf1", "/memory/state.md"): "stable body"})
    msgs = [HumanMessage(content="go")]
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    first = msgs[-1].content
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)
    assert msgs[-1].content == first  # identical bytes → KV-cache stable


def test_fail_soft_on_vfs_error():
    class _Boom:
        def read(self, **k):
            raise RuntimeError("db down")

    msgs = [HumanMessage(content="go")]
    before = [m.content for m in msgs]
    StateEdit(_Boom(), "wf1").apply(msgs, count_tokens=_toklen)  # must NOT raise
    assert [m.content for m in msgs] == before


def test_per_workflow_isolation():
    vfs = _FakeVfs({("wfA", "/memory/state.md"): "state of A",
                    ("wfB", "/memory/state.md"): "state of B"})
    msgs = [HumanMessage(content="go")]
    StateEdit(vfs, "wfB").apply(msgs, count_tokens=_toklen)
    assert "state of B" in msgs[-1].content
    assert "state of A" not in msgs[-1].content


def test_state_injection_survives_an_s1_compaction_pass():
    """state.md is appended AFTER compaction (post-S1), so an S1 pass over the
    SAME message list must not strip it — assert the order matters: compact
    first, inject after, exactly like the _build_context_edits chain."""
    from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit

    big = json.dumps({"status": "success", "error": None, "abstract": "a",
                      "output": {"path": "/data/x.jsonl", "content_type": "table/jsonl",
                                 "data": "y" * 9000}}, ensure_ascii=False)
    msgs = [ToolMessage(content=big, tool_call_id=f"c{i}", name="t") for i in range(6)]
    vfs = _FakeVfs({("wf1", "/memory/state.md"): "RESUME ANCHOR"})

    # Chain order mirrors _build_context_edits: compaction edits run, then the
    # pinned StateEdit appends LAST.
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0).apply(msgs, count_tokens=_toklen)
    StateEdit(vfs, "wf1").apply(msgs, count_tokens=_toklen)

    # The pinned state block is present at the tail and was NOT compacted.
    assert STATE_HEADER in msgs[-1].content
    assert "RESUME ANCHOR" in msgs[-1].content

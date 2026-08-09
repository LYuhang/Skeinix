"""S2b — whole-prefix LLM summary, non-destructive, VFS range cache, hysteresis
including range caching, hysteresis, and token metadata.

All unit-testable WITHOUT a live LLM: the summarizer call is behind an injected
``summarize_fn(prompt) -> str``; the persistent cache is an in-memory fake keyed
by the immutable range ``(thread_id, last_message_id, summarizer_version)``.

S2b collapses the OLDEST contiguous prefix (keeping the pinned head + a recent
live tail) into ONE summary message on the per-call deep-copy when the running
estimate crosses the trigger; raw stays in the checkpointer (non-destructive).
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vibecanvas_api.agents.middleware.s2b_compaction import (
    S2B_SUMMARIZER_VERSION,
    S2bCompactor,
    VfsS2bCache,
    select_summary_range,
)
from vibecanvas_api.agents.token_accounting import record_message_tokens, message_tokens


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _msg(cls, content, *, mid, raw, **kw):
    m = cls(content=content, id=mid, **kw)
    record_message_tokens(m, model="m", form="raw")
    message_tokens(m)["raw"] = raw
    return m


def _history(n_middle=8, raw_each=10_000):
    """system(pinned) + human + n*(ai,tool) turns, every message tagged raw."""
    msgs = [_msg(SystemMessage, "you are an agent", mid="sys", raw=500)]
    msgs.append(_msg(HumanMessage, "build the workflow", mid="h0", raw=500))
    for i in range(n_middle):
        msgs.append(_msg(AIMessage, f"step {i}", mid=f"ai{i}", raw=raw_each))
        env = json.dumps({"status": "success", "abstract": "a",
                          "output": {"path": f"/exec/{i}.log", "content_type": "text/plain",
                                     "data": f"result {i}"}})
        msgs.append(_msg(ToolMessage, env, mid=f"tm{i}", raw=raw_each,
                         tool_call_id=f"tc{i}", name="read_file"))
    return msgs


def _estimate(messages):
    """Mirror estimate_context_tokens: sum recorded raw (all msgs are form=raw here),
    fall back to the S2b summary's recorded compressed size."""
    total = 0
    for m in messages:
        tok = message_tokens(m) or {}
        if tok.get("form") == "compressed" and isinstance(tok.get("compressed"), int):
            total += tok["compressed"]
        elif isinstance(tok.get("raw"), int):
            total += tok["raw"]
        else:
            total += max(0, len(getattr(m, "content", "") or "") // 4)
    return total


class _FakeCache:
    """In-memory stand-in for the VFS range cache."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.reads = 0
        self.writes = 0

    def read(self, range_key):
        self.reads += 1
        return self.store.get(range_key)

    def write(self, range_key, text):
        self.writes += 1
        self.store[range_key] = text


# --------------------------------------------------------------------------- #
# range selection (pure)
# --------------------------------------------------------------------------- #

def test_select_range_picks_oldest_keeps_head_and_tail():
    msgs = _history(n_middle=8, raw_each=10_000)  # ~161k
    rng = select_summary_range(msgs, estimate=_estimate, model="m",
                               target=60_000, live_tail=4, pinned_head=2)
    assert rng is not None
    start, end = rng
    # pinned head (sys + first human) never summarized
    assert start >= 2
    # never touches the live tail (last 4 messages)
    assert end <= len(msgs) - 4 - 1
    # contiguous oldest prefix after the pinned head
    assert start == 2


def test_select_range_stops_when_projected_below_target():
    msgs = _history(n_middle=8, raw_each=10_000)
    rng = select_summary_range(msgs, estimate=_estimate, model="m",
                               target=60_000, live_tail=4, pinned_head=2)
    start, end = rng
    # after replacing [start,end] with a ~0-token summary, the projected estimate
    # (head + tail + remaining) must be < target.
    remaining = msgs[:start] + msgs[end + 1:]
    assert _estimate(remaining) < 60_000


def test_select_range_none_when_nothing_summarizable():
    # only pinned head + a short live tail → no prefix to summarize.
    msgs = _history(n_middle=2, raw_each=10_000)
    rng = select_summary_range(msgs, estimate=_estimate, model="m",
                               target=60_000, live_tail=6, pinned_head=2)
    assert rng is None


# --------------------------------------------------------------------------- #
# hysteresis
# --------------------------------------------------------------------------- #

def test_no_action_between_target_and_trigger():
    # estimate sits between target (60k) and trigger (120k): NO summarization.
    msgs = _history(n_middle=5, raw_each=10_000)  # ~101k
    assert 60_000 < _estimate(msgs) < 120_000
    cache = _FakeCache()
    calls = []
    comp = S2bCompactor(summarize_fn=lambda p: calls.append(p) or "S",
                        cache=cache, thread_id="wf1", model="m",
                        trigger=120_000, target=60_000)
    out = comp.apply(msgs)
    assert calls == []            # never summarized
    assert out is None            # signals "no change"


def test_acts_once_over_trigger_and_drops_below_target():
    msgs = _history(n_middle=10, raw_each=10_000)  # ~201k > 120k
    assert _estimate(msgs) > 120_000
    cache = _FakeCache()
    comp = S2bCompactor(summarize_fn=lambda p: "SUMMARY", cache=cache,
                        thread_id="wf1", model="m", trigger=120_000, target=60_000)
    view = comp.apply(msgs)
    assert view is not None
    assert _estimate(view) < 60_000


# --------------------------------------------------------------------------- #
# replace in the per-call view (non-destructive)
# --------------------------------------------------------------------------- #

def test_replace_collapses_range_keeps_head_and_tail_input_untouched():
    msgs = _history(n_middle=10, raw_each=10_000)
    original_ids = [m.id for m in msgs]
    original_len = len(msgs)
    cache = _FakeCache()
    comp = S2bCompactor(summarize_fn=lambda p: "PREFIX SUMMARY", cache=cache,
                        thread_id="wf1", model="m", trigger=120_000, target=60_000)
    view = comp.apply(msgs)

    # input list identity / content untouched (checkpoint copy is the input)
    assert [m.id for m in msgs] == original_ids
    assert len(msgs) == original_len

    # view: pinned head intact, exactly one summary SystemMessage, live tail intact
    assert isinstance(view[0], SystemMessage) and view[0].id == "sys"
    assert view[1].id == "h0"
    summaries = [m for m in view if getattr(m, "response_metadata", {}).get("s2b")]
    assert len(summaries) == 1
    assert "PREFIX SUMMARY" in summaries[0].content
    # the live tail (last messages) is preserved verbatim
    assert view[-1].id == msgs[-1].id
    assert view[-2].id == msgs[-2].id


def test_summary_message_is_system_message_marked():
    msgs = _history(n_middle=10, raw_each=10_000)
    comp = S2bCompactor(summarize_fn=lambda p: "S", cache=_FakeCache(),
                        thread_id="wf1", model="m", trigger=120_000, target=60_000)
    view = comp.apply(msgs)
    summ = next(m for m in view if getattr(m, "response_metadata", {}).get("s2b"))
    assert isinstance(summ, SystemMessage)


# --------------------------------------------------------------------------- #
# VFS cache hit/miss (frozen-once by range)
# --------------------------------------------------------------------------- #

def test_cache_miss_summarizes_and_writes():
    msgs = _history(n_middle=10, raw_each=10_000)
    cache = _FakeCache()
    calls = []
    comp = S2bCompactor(summarize_fn=lambda p: calls.append(p) or "FRESH",
                        cache=cache, thread_id="wf1", model="m",
                        trigger=120_000, target=60_000)
    comp.apply(msgs)
    assert len(calls) == 1
    assert cache.writes == 1


def test_cache_hit_no_relsm():
    cache = _FakeCache()
    calls = []
    summ = lambda p: calls.append(p) or "ONCE"
    comp = S2bCompactor(summarize_fn=summ, cache=cache, thread_id="wf1",
                        model="m", trigger=120_000, target=60_000)
    comp.apply(_history(n_middle=10, raw_each=10_000))
    # a fresh deep-copy of the same history (mirrors the per-call deepcopy);
    # the persistent cache makes the same range a hit → no re-LLM.
    comp.apply(_history(n_middle=10, raw_each=10_000))
    assert len(calls) == 1


def test_cache_key_uses_range_and_version():
    msgs = _history(n_middle=10, raw_each=10_000)
    cache = _FakeCache()
    comp = S2bCompactor(summarize_fn=lambda p: "S", cache=cache, thread_id="wf1",
                        model="m", trigger=120_000, target=60_000)
    comp.apply(msgs)
    (key,) = list(cache.store.keys())
    assert key.startswith("wf1:")
    assert key.endswith(f":{S2B_SUMMARIZER_VERSION}")


# --------------------------------------------------------------------------- #
# incremental segments
# --------------------------------------------------------------------------- #

def test_incremental_only_summarizes_new_chunk_and_chains():
    # First compaction caches a segment over [0,a]. A LATER, longer history
    # (the prior prefix grew with new turns) must summarize ONLY the new chunk
    # and CHAIN it onto the cached one — never re-summarize the cached prefix.
    cache = _FakeCache()
    prompts = []

    def summ(p):
        prompts.append(p)
        return f"SEG{len(prompts)}"

    comp = S2bCompactor(summarize_fn=summ, cache=cache, thread_id="wf1",
                        model="m", trigger=120_000, target=60_000)

    short = _history(n_middle=10, raw_each=10_000)
    comp.apply(short)
    assert len(prompts) == 1            # first segment computed
    first_segment_count = len(cache.store)

    # the SAME conversation continued: more messages appended after the prior
    # prefix (same ids for the shared older messages, plus new ones).
    grown = _history(n_middle=16, raw_each=10_000)
    view = comp.apply(grown)

    # a NEW segment was computed (incremental), not a full re-summarize.
    assert len(prompts) == 2
    assert len(cache.store) == first_segment_count + 1
    # the chained summary carries BOTH segments.
    summ_msg = next(m for m in view if getattr(m, "response_metadata", {}).get("s2b"))
    assert "SEG1" in summ_msg.content
    assert "SEG2" in summ_msg.content
    # the new prompt summarizes ONLY the new chunk (does not re-feed the cached
    # prefix's already-summarized content — it builds on the prior summary).
    assert "SEG1" in prompts[1]  # prior summary handed in as the running context


# --------------------------------------------------------------------------- #
# estimate after S2b < target
# --------------------------------------------------------------------------- #

def test_estimate_after_s2b_below_target():
    msgs = _history(n_middle=12, raw_each=10_000)
    comp = S2bCompactor(summarize_fn=lambda p: "tiny summary", cache=_FakeCache(),
                        thread_id="wf1", model="m", trigger=120_000, target=60_000)
    view = comp.apply(msgs)
    assert _estimate(view) < 60_000


# --------------------------------------------------------------------------- #
# fail-soft
# --------------------------------------------------------------------------- #

def test_failsoft_summarize_raises_returns_none():
    def boom(p):
        raise RuntimeError("model down")

    comp = S2bCompactor(summarize_fn=boom, cache=_FakeCache(), thread_id="wf1",
                        model="m", trigger=120_000, target=60_000)
    out = comp.apply(_history(n_middle=10, raw_each=10_000))
    assert out is None  # fall back to S1 only; turn not broken


def test_failsoft_cache_error_still_summarizes():
    class _BadCache:
        def read(self, k):
            raise RuntimeError("vfs down")

        def write(self, k, v):
            raise RuntimeError("vfs down")

    comp = S2bCompactor(summarize_fn=lambda p: "OK", cache=_BadCache(),
                        thread_id="wf1", model="m", trigger=120_000, target=60_000)
    view = comp.apply(_history(n_middle=10, raw_each=10_000))
    # cache errors are swallowed; S2b still produces a view (fail-soft).
    assert view is not None
    assert _estimate(view) < 60_000


def test_inert_without_summarize_fn_or_cache():
    msgs = _history(n_middle=10, raw_each=10_000)
    assert S2bCompactor(summarize_fn=None, cache=_FakeCache(), thread_id="w",
                        model="m", trigger=120_000, target=60_000).apply(msgs) is None
    assert S2bCompactor(summarize_fn=lambda p: "s", cache=None, thread_id="w",
                        model="m", trigger=120_000, target=60_000).apply(msgs) is None


# --------------------------------------------------------------------------- #
# S2bSummary value object + VfsS2bCache
# --------------------------------------------------------------------------- #

def test_vfs_cache_roundtrip_and_path():
    class _FakeVfs:
        def __init__(self):
            self.arts = {}

        def upsert_artifact(self, *, wf_id, path, content, content_type, abstract):
            self.arts[(wf_id, path)] = content

        def read(self, *, wf_id, path):
            c = self.arts.get((wf_id, path))
            if c is None:
                return None
            return type("E", (), {"content": c})()

    vfs = _FakeVfs()
    cache = VfsS2bCache(vfs, "wf1")
    key = f"wf1:tm5:{S2B_SUMMARIZER_VERSION}"
    assert cache.read(key) is None
    cache.write(key, "segment summary")
    assert cache.read(key) == "segment summary"
    # path lives under the hidden compaction tier
    (path,) = [p for (_w, p) in vfs.arts]
    assert path.startswith("/exec/__compaction__/")

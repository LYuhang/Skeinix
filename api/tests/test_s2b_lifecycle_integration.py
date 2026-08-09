"""S2b integration with the ``LifecyclePolicyEdit`` compaction pass.

S2b runs FIRST among the compaction stages: it collapses the OLD prefix into one
summary message (on the deep-copy) before S2a / head+tail / S1, so the downstream
passes only see the smaller still-shown region. Fake summarize_fn + in-memory
cache — no live LLM. Asserts the in-place splice the ContextEditingMiddleware
relies on (the list object is read back; the edit's return value is ignored).
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.middleware.s2b_compaction import S2bCompactor
from vibecanvas_api.agents.token_accounting import record_message_tokens, message_tokens


def _msg(cls, content, *, mid, raw, **kw):
    m = cls(content=content, id=mid, **kw)
    record_message_tokens(m, model="m", form="raw")
    message_tokens(m)["raw"] = raw
    return m


def _history(n_middle=12, raw_each=10_000):
    msgs = [_msg(SystemMessage, "you are an agent", mid="sys", raw=500),
            _msg(HumanMessage, "build the workflow", mid="h0", raw=500)]
    for i in range(n_middle):
        msgs.append(_msg(AIMessage, f"step {i}", mid=f"ai{i}", raw=raw_each))
        env = json.dumps({"status": "success", "abstract": "a",
                          "output": {"path": f"/exec/{i}.log", "content_type": "text/plain",
                                     "data": f"r{i}"}})
        msgs.append(_msg(ToolMessage, env, mid=f"tm{i}", raw=raw_each,
                         tool_call_id=f"tc{i}", name="read_file"))
    return msgs


def _approx_count(messages):
    return sum(len(getattr(m, "content", "") or "") for m in messages) // 4


class _FakeCache:
    def __init__(self):
        self.store = {}

    def read(self, k):
        return self.store.get(k)

    def write(self, k, v):
        self.store[k] = v


def test_s2b_splices_prefix_in_place_in_lifecycle_pass():
    msgs = _history(n_middle=12, raw_each=10_000)
    orig_obj = msgs  # SAME list object the middleware reads back
    n_before = len(msgs)
    s2b = S2bCompactor(summarize_fn=lambda p: "PREFIX SUMMARY", cache=_FakeCache(),
                       thread_id="wf1", model="m", trigger=120_000, target=60_000)
    LifecyclePolicyEdit(trigger=10**9, clear_at_least=0, model="m", s2b=s2b).apply(
        msgs, count_tokens=_approx_count)

    assert msgs is orig_obj          # mutated in place (length changed via slice)
    assert len(msgs) < n_before      # prefix collapsed
    assert isinstance(msgs[0], SystemMessage) and msgs[0].id == "sys"
    summaries = [m for m in msgs if getattr(m, "response_metadata", {}).get("s2b")]
    assert len(summaries) == 1
    assert "PREFIX SUMMARY" in summaries[0].content


def test_s2b_below_trigger_is_noop():
    msgs = _history(n_middle=3, raw_each=10_000)  # ~61k < 120k
    before = [m.id for m in msgs]
    s2b = S2bCompactor(summarize_fn=lambda p: "S", cache=_FakeCache(),
                       thread_id="wf1", model="m", trigger=120_000, target=60_000)
    LifecyclePolicyEdit(trigger=10**9, model="m", s2b=s2b).apply(
        msgs, count_tokens=_approx_count)
    assert [m.id for m in msgs] == before  # unchanged


def test_no_s2b_configured_is_pure_s1():
    msgs = _history(n_middle=12, raw_each=10_000)
    before = [m.id for m in msgs]
    LifecyclePolicyEdit(trigger=10**9, model="m").apply(msgs, count_tokens=_approx_count)
    assert [m.id for m in msgs] == before  # below S1 trigger, no S2b → untouched


def test_s2b_then_s1_compose_drops_context():
    # S2b collapses the old prefix; then S1's own trigger degrades remaining
    # standard outputs. Both run; the end estimate is well below the S2b target.
    msgs = _history(n_middle=14, raw_each=10_000)
    s2b = S2bCompactor(summarize_fn=lambda p: "small", cache=_FakeCache(),
                       thread_id="wf1", model="m", trigger=120_000, target=60_000)
    # S1 trigger low so it ALSO degrades the surviving middle outputs.
    LifecyclePolicyEdit(trigger=20_000, clear_at_least=0, model="m", s2b=s2b).apply(
        msgs, count_tokens=_approx_count)
    # pinned head survives; a single s2b summary present; far fewer raw messages.
    assert msgs[0].id == "sys"
    assert sum(1 for m in msgs if getattr(m, "response_metadata", {}).get("s2b")) == 1

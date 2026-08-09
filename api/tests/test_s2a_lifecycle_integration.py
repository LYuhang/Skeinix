"""S2a integration with the ``LifecyclePolicyEdit`` compaction pass.

S2a runs BEFORE the S1 recency-cut so that when an oversize output is later
degraded to ``reference``, the stub prefers the now-present ``llm_abstract``.
Fake summarize_fn + an in-memory cache — no live LLM.
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.middleware.s2a_compaction import S2aCompactor
from vibecanvas_api.agents.token_accounting import record_message_tokens, message_tokens


def _env(path, ct, data, abstract="cheap"):
    return json.dumps({"status": "success", "error": None, "abstract": abstract,
                       "output": {"path": path, "content_type": ct, "data": data}},
                      ensure_ascii=False)


def _toklen(messages):
    return sum(len(getattr(m, "content", "") or "") for m in messages) // 4


class _FakeCache:
    def __init__(self):
        self.store = {}
        self.computes = 0

    def read(self, tcid):
        return self.store.get(tcid)

    def write(self, tcid, text):
        self.store[tcid] = text


def _oversize_history(tcid="tc_big"):
    ai = AIMessage(content="", tool_calls=[
        {"id": tcid, "name": "read_file", "args": {"path": "/exec/big.log"}}])
    big = _env("/exec/big.log", "text/plain", data="z" * 60000)
    tool = ToolMessage(content=big, tool_call_id=tcid, name="read_file")
    # mark it oversize for is_oversize's token gate
    record_message_tokens(tool, model="m", form="raw")
    message_tokens(tool)["raw"] = 20000
    return [HumanMessage(content="find the failure"), ai, tool]


def test_oversize_flows_through_pass_fills_and_caches():
    cache = _FakeCache()
    calls = []

    def fake_summarize(prompt):
        calls.append(prompt)
        return "GIST: failed at step 3"

    s2a = S2aCompactor(summarize_fn=fake_summarize, cache=cache, cap=8000, model="m")
    msgs = _oversize_history()
    # trigger high enough that S1 does NOT degrade — we isolate the S2a fill.
    LifecyclePolicyEdit(trigger=10**9, clear_at_least=0, model="m", s2a=s2a).apply(
        msgs, count_tokens=_toklen)

    env = json.loads(msgs[2].content)
    assert env["llm_abstract"] == "GIST: failed at step 3"
    assert cache.store["tc_big"] == "GIST: failed at step 3"
    assert len(calls) == 1
    assert isinstance(message_tokens(msgs[2])["compressed"], int)


def test_second_pass_is_cache_hit_no_recompute():
    cache = _FakeCache()
    calls = []

    def fake_summarize(prompt):
        calls.append(prompt)
        return "GIST once"

    s2a = S2aCompactor(summarize_fn=fake_summarize, cache=cache, cap=8000, model="m")
    LifecyclePolicyEdit(trigger=10**9, model="m", s2a=s2a).apply(
        _oversize_history(), count_tokens=_toklen)
    # a fresh deep-copy of the same history (mirrors the per-call deepcopy) —
    # the cache persists across the simulated turns, so no recompute.
    LifecyclePolicyEdit(trigger=10**9, model="m", s2a=s2a).apply(
        _oversize_history(), count_tokens=_toklen)
    assert len(calls) == 1  # frozen-once across turns via the persistent cache


def test_s2a_then_s1_degrade_reference_uses_llm_abstract():
    cache = _FakeCache()
    s2a = S2aCompactor(summarize_fn=lambda p: "TARGETED GIST", cache=cache, cap=8000, model="m")
    msgs = _oversize_history()
    # add several FRESH text/plain outputs after so the oversize one ages out of
    # the fresh_k window and gets degraded to its reference form.
    for i in range(6):
        msgs.append(ToolMessage(
            content=_env(f"/exec/x{i}.log", "text/plain", data="y" * 1000),
            tool_call_id=f"tc{i}", name="read_file"))
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0, model="m", s2a=s2a).apply(
        msgs, count_tokens=_toklen)

    # the oversize msg (index 2) was degraded to reference; its abstract must be
    # the S2a llm_abstract, not the cheap deterministic one.
    obj = json.loads(msgs[2].content)
    assert obj.get("abstract") == "TARGETED GIST"


def test_no_s2a_configured_is_pure_s1():
    # Without an s2a compactor, behaviour is exactly the shipped S1 path.
    msgs = _oversize_history()
    before = msgs[2].content
    LifecyclePolicyEdit(trigger=10**9, model="m").apply(msgs, count_tokens=_toklen)
    assert msgs[2].content == before  # below trigger, no degrade, no S2a

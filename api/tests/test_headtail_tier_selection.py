"""Tier selection in ``LifecyclePolicyEdit``:
  - fresh + LARGE (full_tokens>threshold, text/log) → head+tail, reading the FULL
    body from VFS by path (the DEFAULT — head+tail, deterministic, no LLM).
  - fresh + small → full inline (unchanged).
  - aging (past the fresh window) → S1 cleared reference stub.
  - the "don't hide the original" property: a fresh 50k-token omitted output →
    the agent sees head+tail+path, NOT a bare data:None.
"""
import json

from langchain_core.messages import ToolMessage

from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit


class _FakeVfsReader:
    """Read full bodies back by path (the producer wrote them before omitting)."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        return self.store.get(path)


def _large_env(path, ct="text/plain", full_tokens=20000, full_chars=80000):
    # A LARGE output: producer omitted inline data, recorded full size + path.
    return json.dumps({
        "status": "success", "error": None, "abstract": "cheap abstract",
        "output": {"path": path, "content_type": ct,
                   "full_tokens": full_tokens, "full_chars": full_chars},
    }, ensure_ascii=False)


def _small_env(path, ct="text/plain", data="tiny"):
    return json.dumps({
        "status": "success", "error": None, "abstract": "cheap",
        "output": {"path": path, "content_type": ct, "data": data},
    }, ensure_ascii=False)


def _tool(content, tcid="t"):
    return ToolMessage(content=content, tool_call_id=tcid, name="read_file")


def _toklen(messages):
    return sum(len(getattr(m, "content", "") or "") for m in messages) // 4


def test_fresh_large_head_tail_in_place():
    body = "\n".join(f"log line {i}" for i in range(5000))
    reader = _FakeVfsReader({"/exec/big.log": body})
    msgs = [_tool(_large_env("/exec/big.log"))]
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=reader, headtail_threshold=8000,
                        headtail_head_tokens=50, headtail_tail_tokens=20).apply(
        msgs, count_tokens=_toklen)
    env = json.loads(msgs[0].content)
    data = env["output"]["data"]
    assert "log line 0" in data                 # head from the FULL VFS body
    assert "log line 4999" in data              # tail
    assert "tokens elided" in data and "/exec/big.log" in data
    assert reader.reads == ["/exec/big.log"]    # it READ the full from VFS
    # the abstract+path are preserved (still re-readable)
    assert env["output"]["path"] == "/exec/big.log"


def test_fresh_small_stays_full():
    reader = _FakeVfsReader({})
    msgs = [_tool(_small_env("/exec/s.log", data="the whole tiny body"))]
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=reader, headtail_threshold=8000).apply(
        msgs, count_tokens=_toklen)
    env = json.loads(msgs[0].content)
    assert env["output"]["data"] == "the whole tiny body"
    assert reader.reads == []                    # small → no VFS read, no head+tail


def test_dont_hide_original_property_50k():
    # The load-bearing property: a fresh 50k-token output is NOT a bare data:None;
    # the agent sees head+tail+path.
    body = "X" + "\n".join(f"row {i}" for i in range(50000))
    reader = _FakeVfsReader({"/exec/huge.log": body})
    msgs = [_tool(_large_env("/exec/huge.log", full_tokens=50000, full_chars=200000))]
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=reader, headtail_threshold=8000,
                        headtail_head_tokens=200, headtail_tail_tokens=80).apply(
        msgs, count_tokens=_toklen)
    env = json.loads(msgs[0].content)
    data = env["output"]["data"]
    assert data is not None and isinstance(data, str) and data != ""
    assert "row 0" in data                       # start visible
    assert "/exec/huge.log" in data              # knows where the full is
    assert "tokens elided" in data


def test_vfs_miss_falls_back_to_abstract_path_stub():
    reader = _FakeVfsReader({})                   # path not in VFS → miss
    msgs = [_tool(_large_env("/exec/gone.log"))]
    before = msgs[0].content
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=reader, headtail_threshold=8000).apply(
        msgs, count_tokens=_toklen)
    # fail-soft: the envelope is left intact (abstract+path still reachable).
    assert msgs[0].content == before


def test_no_vfs_reader_is_inert():
    msgs = [_tool(_large_env("/exec/big.log"))]
    before = msgs[0].content
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=None, headtail_threshold=8000).apply(
        msgs, count_tokens=_toklen)
    assert msgs[0].content == before


def test_under_threshold_not_head_tailed():
    reader = _FakeVfsReader({"/exec/m.log": "body"})
    msgs = [_tool(_large_env("/exec/m.log", full_tokens=5000))]  # < 8000 threshold
    before = msgs[0].content
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=reader, headtail_threshold=8000).apply(
        msgs, count_tokens=_toklen)
    assert msgs[0].content == before
    assert reader.reads == []


def test_link_cloud_table_not_head_tailed():
    # A tiny ref (a URL) is never head/tailed even if it claimed big full_tokens.
    reader = _FakeVfsReader({"https://x": "body"})
    msgs = [_tool(_large_env("https://x", ct="link/cloud_table", full_tokens=50000))]
    before = msgs[0].content
    LifecyclePolicyEdit(trigger=10**9, vfs_reader=reader, headtail_threshold=8000).apply(
        msgs, count_tokens=_toklen)
    assert msgs[0].content == before


def test_aging_head_tailed_then_degrades_to_reference():
    # tier-2 → tier-3: a head-tailed output that ages out of the fresh window gets
    # S1-degraded to the cleared reference stub (head+tail is NOT terminal).
    body = "\n".join(f"line {i}" for i in range(5000))
    reader = _FakeVfsReader({"/exec/old.log": body})
    msgs = [_tool(_large_env("/exec/old.log"), tcid="old")]
    # add fresh text/plain outputs so the big one ages past fresh_k=4
    for i in range(6):
        msgs.append(_tool(_small_env(f"/exec/x{i}.log", data="y" * 2000), tcid=f"x{i}"))
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0, vfs_reader=reader,
                        headtail_threshold=8000, headtail_head_tokens=40,
                        headtail_tail_tokens=20).apply(msgs, count_tokens=_toklen)
    obj = json.loads(msgs[0].content)
    # degraded to reference: no inline data, path preserved
    assert "data" not in obj["output"]
    assert obj["output"]["path"] == "/exec/old.log"

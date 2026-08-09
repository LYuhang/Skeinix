"""S2a production wiring: assemble the compactor only
when BOTH the model (from agent_cfg) AND the VFS cache (vfs_store + wf_id) are
reachable at the compaction seam; otherwise S2a is inert (pure S1)."""

from vibecanvas_api.agent import (
    _build_context_edits,
    _build_s2a_compactor,
)
from vibecanvas_api.config import AgentConfig
from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.middleware.s2a_compaction import S2aCompactor, VfsS2aCache


class _FakeVfs:
    def __init__(self):
        self.written = {}

    def read(self, *, wf_id, path):
        body = self.written.get((wf_id, path))
        if body is None:
            return None
        return type("E", (), {"content": body})()

    def upsert_artifact(self, *, wf_id, path, content, content_type="text/plain", abstract=""):
        self.written[(wf_id, path)] = content


def _cfg(s2a_enabled=True):
    # S2a is opt-in; enable it explicitly in wiring tests that
    # assert the compactor is built. Default (no flag) → S2a inert (head+tail).
    return AgentConfig({"model": "openai:gpt-4o-mini", "api_key": "sk-x",
                        "compaction": {"s2a_oversize_tokens": 6000,
                                       "s2a_enabled": s2a_enabled}})


def test_no_vfs_store_yields_inert_s2a():
    edits = _build_context_edits(_cfg(), vfs_store=None, wf_id="wf1")
    assert isinstance(edits[0], LifecyclePolicyEdit)
    assert edits[0].s2a is None


def test_no_wf_id_yields_inert_s2a():
    edits = _build_context_edits(_cfg(), vfs_store=_FakeVfs(), wf_id="")
    assert edits[0].s2a is None


def test_s2a_disabled_by_default_yields_inert_s2a():
    # No s2a_enabled flag → head+tail is the default, S2a (LLM gist) is OFF.
    cfg = AgentConfig({"model": "openai:gpt-4o-mini", "api_key": "sk-x"})
    assert _build_s2a_compactor(cfg, _FakeVfs(), "wf1") is None
    edits = _build_context_edits(cfg, vfs_store=_FakeVfs(), wf_id="wf1")
    assert edits[0].s2a is None
    # but the head+tail tier IS wired (vfs_reader present).
    assert edits[0].vfs_reader is not None


def test_compactor_built_with_cap_and_cache_when_reachable():
    s2a = _build_s2a_compactor(_cfg(), _FakeVfs(), "wf1")
    assert isinstance(s2a, S2aCompactor)
    assert s2a.cap == 6000
    assert isinstance(s2a.cache, VfsS2aCache)
    assert callable(s2a.summarize_fn)
    assert s2a.vfs_reader is not None      # §4.1a: gist reads FULL body from VFS


def test_vfs_cache_roundtrips_by_tool_call_id():
    vfs = _FakeVfs()
    cache = VfsS2aCache(vfs, "wf1")
    assert cache.read("tc1") is None
    cache.write("tc1", "the gist")
    assert cache.read("tc1") == "the gist"
    # stored at the conventional path keyed by tool_call_id
    assert ("wf1", S2aCompactor.cache_path("tc1")) in vfs.written
    assert S2aCompactor.cache_path("tc1") == "/exec/__compaction__/tc1.txt"


def test_vfs_cache_read_is_failsoft():
    class _Boom:
        def read(self, **k):
            raise RuntimeError("db down")

        def upsert_artifact(self, **k):
            raise RuntimeError("db down")

    cache = VfsS2aCache(_Boom(), "wf1")
    assert cache.read("tc1") is None        # swallowed → miss
    cache.write("tc1", "x")                 # swallowed → no raise

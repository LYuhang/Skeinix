"""S2b production wiring: assemble the safety-net compactor when
``s2b_enabled`` (DEFAULT True) AND the model (from agent_cfg) AND the VFS range
cache (vfs_store + wf_id) are reachable at the compaction seam; otherwise inert
(pure S1)."""
from vibecanvas_api.agent import (
    _build_context_edits,
    _build_s2b_compactor,
)
from vibecanvas_api.config import AgentConfig
from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.middleware.s2b_compaction import (
    S2B_SUMMARIZER_VERSION,
    S2bCompactor,
    VfsS2bCache,
)


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


def _cfg(**compaction):
    return AgentConfig({"model": "openai:gpt-4o-mini", "api_key": "sk-x",
                        "compaction": compaction})


def test_s2b_enabled_by_default():
    # No flag → S2b ON (the safety net), unlike opt-in S2a.
    s2b = _build_s2b_compactor(_cfg(), _FakeVfs(), "wf1")
    assert isinstance(s2b, S2bCompactor)
    assert isinstance(s2b.cache, VfsS2bCache)
    assert callable(s2b.summarize_fn)
    assert s2b.thread_id == "wf1"
    assert s2b.trigger == 120_000
    assert s2b.target == 60_000


def test_s2b_config_overrides():
    s2b = _build_s2b_compactor(
        _cfg(summary_trigger_tokens=200_000, summary_target_tokens=90_000,
             summary_pinned_head=3, summary_live_tail=8), _FakeVfs(), "wf1")
    assert s2b.trigger == 200_000
    assert s2b.target == 90_000
    assert s2b.pinned_head == 3
    assert s2b.live_tail == 8


def test_s2b_disabled_yields_inert():
    s2b = _build_s2b_compactor(_cfg(s2b_enabled=False), _FakeVfs(), "wf1")
    assert s2b is None
    edits = _build_context_edits(_cfg(s2b_enabled=False),
                                 vfs_store=_FakeVfs(), wf_id="wf1")
    assert edits[0].s2b is None


def test_no_vfs_store_or_wf_id_yields_inert():
    assert _build_s2b_compactor(_cfg(), None, "wf1") is None
    assert _build_s2b_compactor(_cfg(), _FakeVfs(), "") is None
    edits = _build_context_edits(_cfg(), vfs_store=None, wf_id="wf1")
    assert isinstance(edits[0], LifecyclePolicyEdit)
    assert edits[0].s2b is None


def test_s2b_wired_into_lifecycle_edit_when_reachable():
    edits = _build_context_edits(_cfg(), vfs_store=_FakeVfs(), wf_id="wf1")
    assert isinstance(edits[0].s2b, S2bCompactor)


def test_vfs_range_cache_roundtrips_and_path():
    vfs = _FakeVfs()
    cache = VfsS2bCache(vfs, "wf1")
    key = f"wf1:tm9:{S2B_SUMMARIZER_VERSION}"
    assert cache.read(key) is None
    cache.write(key, "segment")
    assert cache.read(key) == "segment"
    assert ("wf1", VfsS2bCache.cache_path(key)) in vfs.written
    assert VfsS2bCache.cache_path(key) == "/exec/__compaction__/summary_tm9.txt"


def test_vfs_range_cache_failsoft():
    class _Boom:
        def read(self, **k):
            raise RuntimeError("db down")

        def upsert_artifact(self, **k):
            raise RuntimeError("db down")

    cache = VfsS2bCache(_Boom(), "wf1")
    assert cache.read("wf1:x:v1") is None
    cache.write("wf1:x:v1", "y")  # swallowed → no raise

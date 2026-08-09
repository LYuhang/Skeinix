"""Tasks C4/C5 — B2 boundary plan + content_compress candidates + project pipeline."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens, current_form
from vibecanvas_api.agents.middleware import compaction_engine as ce


def _item(uid, round_index, raw_tokens, *, role="tool", is_error=False, tool=None, path=None):
    m = new_meta(uid, round_index)
    stamp_tokens(m, "content", raw_tokens)
    stamp_tokens(m, "content_abbreviation", max(1, raw_tokens // 50))
    stamp_tokens(m, "content_abstract", 5)
    return {"meta": m, "is_error": is_error, "tool": tool, "path": path,
            "stale": False, "role": role}


# ── C5: content_compress candidates ──
def test_compress_candidate_huge_single_output():
    cfg = CompactionV2Config({})  # compress_single_tokens 30000
    huge = _item("h", 5, 50000)
    small = _item("s", 5, 1000)
    cands = ce.compress_candidates([huge, small], pressure=0.1, cfg=cfg)
    assert huge in cands and small not in cands


def test_compress_candidate_under_pressure():
    cfg = CompactionV2Config({})  # compress_pressure 0.8
    it = _item("i", 5, 10000)
    assert ce.compress_candidates([it], pressure=0.85, cfg=cfg) == [it]
    assert ce.compress_candidates([it], pressure=0.5, cfg=cfg) == []


def test_compress_skips_errors():
    cfg = CompactionV2Config({})
    err = _item("e", 5, 50000, is_error=True)
    assert ce.compress_candidates([err], pressure=0.9, cfg=cfg) == []


# ── C4: B2 boundary plan ──
def test_b2_none_below_summary_pressure():
    cfg = CompactionV2Config({})
    items = [_item(f"i{r}", r, 1000, role="user") for r in range(0, 5)]
    assert ce.b2_plan(items, current_round=4, window=10_000_000, cfg=cfg) is None


def test_b2_summarizes_old_prefix_excluding_pinned_round0():
    cfg = CompactionV2Config({"pressure_summary": 0.0, "protect_recent_rounds": 2})
    items = [_item(f"i{r}", r, 5000, role="user") for r in range(0, 8)]  # rounds 0..7
    plan = ce.b2_plan(items, current_round=7, window=1000, cfg=cfg)
    assert plan is not None
    summarized_rounds = {it["meta"]["round_index"] for it in plan["summarize"]}
    assert 0 not in summarized_rounds                  # pinned first exchange survives
    assert 1 in summarized_rounds                      # old prefix summarized
    assert all(r <= 5 for r in summarized_rounds)      # protect last 2 (rounds 6,7)
    # boundary uid = the last summarized round's uid
    last = max(plan["summarize"], key=lambda it: it["meta"]["round_index"])
    assert plan["boundary_uid"] == last["meta"]["unique_id"]
    assert plan["survivor"] is not None


# ── project pipeline (§4.0 order) ──
def test_project_runs_full_pipeline():
    cfg = CompactionV2Config({"clear_at_least": 100, "protect_recent_rounds": 2})
    items = [_item(f"i{r}", r, 5000, role="user") for r in range(0, 10)]
    out = ce.project(items, current_round=20, window=2000, cfg=cfg)
    assert "resegment" in out and "compress" in out and "b2" in out
    # pinned round 0 survived as content
    assert current_form(items[0]["meta"]) == "content"

"""Batched re-segmentation, reclamation floor, and B1 abstraction."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens, current_form, context_size
from vibecanvas_api.agents.middleware import compaction_engine as ce


def _item(uid, round_index, raw_tokens):
    m = new_meta(uid, round_index)
    stamp_tokens(m, "content", raw_tokens)
    stamp_tokens(m, "content_abbreviation", max(1, raw_tokens // 50))
    stamp_tokens(m, "content_abstract", 5)
    return {"meta": m, "is_error": False, "tool": None, "path": None, "stale": False}


def _metas(items):
    return [it["meta"] for it in items]


def test_should_resegment_on_schedule_and_pressure():
    cfg = CompactionV2Config({})  # resegment_every_rounds=8, pressure_abstract .5, summary .8
    assert ce.should_resegment(current_round=8, prev_pressure=0.1, cur_pressure=0.1, cfg=cfg)
    assert ce.should_resegment(current_round=3, prev_pressure=0.49, cur_pressure=0.51, cfg=cfg)  # crossed .5
    assert not ce.should_resegment(current_round=3, prev_pressure=0.1, cur_pressure=0.2, cfg=cfg)


def test_resegment_skipped_below_clear_at_least():
    cfg = CompactionV2Config({"clear_at_least": 20000})
    items = [_item(f"y{r}", r, 1500) for r in range(1, 4)]  # small/young → nothing to reclaim
    res = ce.resegment(items, current_round=2, window=1_000_000, cfg=cfg)
    assert res["applied"] is False
    assert all(current_form(it["meta"]) == "content" for it in items)


def test_resegment_applies_b1_under_pressure():
    cfg = CompactionV2Config({"clear_at_least": 100, "protect_recent_rounds": 1})
    items = [_item(f"o{r}", r, 5000) for r in range(1, 8)]   # rounds 1..7, big
    window = 1000                                            # target = 500; size ~ huge
    res = ce.resegment(items, current_round=50, window=window, cfg=cfg)
    assert res["applied"] is True
    # oldest items squeezed to abstract; size pulled toward target
    forms = [current_form(it["meta"]) for it in items]
    assert "content_abstract" in forms
    assert context_size(_metas(items)) < 7 * 5000


def test_b1_protects_recent_window():
    cfg = CompactionV2Config({"clear_at_least": 100, "protect_recent_rounds": 2})
    items = [_item(f"i{r}", r, 5000) for r in range(1, 11)]
    ce.resegment(items, current_round=10, window=500, cfg=cfg, force=True)
    # the most-recent 2 rounds (age <= 2 → rounds 9,10) stay content
    recent = {it["meta"]["unique_id"]: current_form(it["meta"]) for it in items}
    assert recent["i9"] == "content"
    assert recent["i10"] == "content"

"""Task C1 — compaction engine: tiers + form selection (A age decay)."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens, set_current_form, current_form
from vibecanvas_api.agents.middleware import compaction_engine as ce

CFG = CompactionV2Config({})  # defaults: protect_recent_rounds=3, S full_rounds=8


def _item(uid, round_index, raw_tokens, *, is_error=False, tool=None, path=None, stale=False):
    m = new_meta(uid, round_index)
    stamp_tokens(m, "content", raw_tokens)
    stamp_tokens(m, "content_abbreviation", max(1, raw_tokens // 50))
    stamp_tokens(m, "content_abstract", 10)
    return {"meta": m, "is_error": is_error, "tool": tool, "path": path, "stale": stale}


def test_none_band_never_decays():
    it = _item("a", 0, 500)            # < 2000 → none tier
    ce.apply_age_decay([it], current_round=100, cfg=CFG)
    assert current_form(it["meta"]) == "content"


def test_S_tier_decays_after_budget():
    young = _item("y", 11, 5000)       # S tier, full_rounds=8
    old = _item("o", 1, 5000)          # round 1 (not the pinned round 0)
    ce.apply_age_decay([young, old], current_round=16, cfg=CFG)  # young age 5, old age 15
    assert current_form(young["meta"]) == "content"             # within budget
    assert current_form(old["meta"]) == "content_abbreviation"  # past budget


def test_protect_recent_window_overrides_budget():
    xl = _item("x", 2, 600000)         # XL full_rounds=1, but age 2 <= protect 3
    ce.apply_age_decay([xl], current_round=4, cfg=CFG)
    assert current_form(xl["meta"]) == "content"
    ce.apply_age_decay([xl], current_round=8, cfg=CFG)  # now age 6 > protect 3
    assert current_form(xl["meta"]) == "content_abbreviation"


def test_pin_first_exchange():
    first = _item("f", 0, 600000)
    ce.apply_age_decay([first], current_round=50, cfg=CFG)
    assert current_form(first["meta"]) == "content"


def test_error_resists_age_decay_by_default():
    err = _item("e", 0, 5000, is_error=True)
    ce.apply_age_decay([err], current_round=50, cfg=CFG)
    assert current_form(err["meta"]) == "content"


def test_monotonic_never_un_degrades():
    it = _item("m", 0, 5000)
    set_current_form(it["meta"], "content_abstract")   # already more degraded
    ce.apply_age_decay([it], current_round=50, cfg=CFG)
    assert current_form(it["meta"]) == "content_abstract"  # A does not raise it back to abbreviation

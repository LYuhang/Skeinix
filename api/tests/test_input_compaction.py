"""Deterministic tool-input compaction."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens, current_form, set_current_form
from vibecanvas_api.agents.middleware import compaction_engine as ce

CFG = CompactionV2Config({})  # protect_recent_rounds=3, S full_rounds=8


def _iitem(uid, round_index, raw, *, is_write=False, path=None):
    m = new_meta(uid, round_index)
    stamp_tokens(m, "content", raw)
    stamp_tokens(m, "content_abbreviation", max(1, raw // 50))
    stamp_tokens(m, "ref", 5)
    return {"meta": m, "is_write_content": is_write, "path": path, "is_error": False}


def test_small_arg_never_decays():
    it = _iitem("a", 1, 500)               # none tier
    ce.apply_input_decay([it], current_round=100, cfg=CFG)
    assert current_form(it["meta"]) == "content"


def test_large_arg_degrades_to_abbreviation():
    it = _iitem("a", 1, 5000)              # S tier, full_rounds 8
    ce.apply_input_decay([it], current_round=50, cfg=CFG)
    assert current_form(it["meta"]) == "content_abbreviation"


def test_write_content_degrades_straight_to_ref():
    it = _iitem("w", 1, 5000, is_write=True, path="/mount/f.txt")
    ce.apply_input_decay([it], current_round=50, cfg=CFG)
    assert current_form(it["meta"]) == "ref"      # lossless — VFS copy exists


def test_protected_recent_input_kept_full():
    it = _iitem("r", 48, 5000, is_write=True, path="/p")
    ce.apply_input_decay([it], current_round=50, cfg=CFG)  # age 2 <= protect 3
    assert current_form(it["meta"]) == "content"


def test_input_monotonic():
    it = _iitem("m", 1, 5000)
    set_current_form(it["meta"], "ref")
    ce.apply_input_decay([it], current_round=50, cfg=CFG)
    assert current_form(it["meta"]) == "ref"      # never un-degrades

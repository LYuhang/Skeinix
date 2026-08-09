"""Auxiliary multimodal-lane decay."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens, set_current_form, current_form
from vibecanvas_api.agents.middleware import compaction_engine as ce

CFG = CompactionV2Config({})  # aux_full_rounds = 2


def _aux(uid, round_index):
    m = new_meta(uid, round_index)
    stamp_tokens(m, "auxiliary", 1200)
    stamp_tokens(m, "ref", 8)
    set_current_form(m, "auxiliary")  # starts live
    return {"meta": m}


def test_live_within_budget():
    it = _aux("a", 8)
    ce.apply_aux_decay([it], current_round=9, cfg=CFG, multimodal=True)  # age 1 <= 2
    assert current_form(it["meta"]) == "auxiliary"


def test_decays_to_caption_after_budget():
    it = _aux("a", 1)
    ce.apply_aux_decay([it], current_round=10, cfg=CFG, multimodal=True)  # age 9 > 2
    assert current_form(it["meta"]) == "ref"  # caption stub


def test_non_multimodal_caption_immediately():
    it = _aux("a", 9)
    ce.apply_aux_decay([it], current_round=9, cfg=CFG, multimodal=False)
    assert current_form(it["meta"]) == "ref"

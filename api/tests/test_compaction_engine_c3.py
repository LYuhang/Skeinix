"""Stale-read supersession by input path."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens, current_form
from vibecanvas_api.agents.middleware import compaction_engine as ce

CFG = CompactionV2Config({})  # stale_on_reread_tools default includes read_file/get_workflow


def _item(uid, round_index, *, tool=None, path=None):
    m = new_meta(uid, round_index)
    stamp_tokens(m, "content", 5000)
    stamp_tokens(m, "ref", 5)
    return {"meta": m, "is_error": False, "tool": tool, "path": path, "stale": tool in CFG.stale_on_reread_tools}


def test_earlier_read_of_same_path_superseded():
    a = _item("a", 1, tool="read_file", path="/f.txt")
    b = _item("b", 5, tool="read_file", path="/f.txt")
    ce.apply_supersession([a, b], cfg=CFG)
    assert current_form(a["meta"]) == "ref"        # earlier read stale
    assert current_form(b["meta"]) == "content"    # latest kept


def test_different_paths_not_superseded():
    a = _item("a", 1, tool="read_file", path="/x.txt")
    b = _item("b", 5, tool="read_file", path="/y.txt")
    ce.apply_supersession([a, b], cfg=CFG)
    assert current_form(a["meta"]) == "content"
    assert current_form(b["meta"]) == "content"


def test_overrides_protect_window():
    # both reads recent (rounds 9,10), but supersession is lossless → overrides protect
    a = _item("a", 9, tool="read_file", path="/f.txt")
    b = _item("b", 10, tool="read_file", path="/f.txt")
    ce.apply_supersession([a, b], cfg=CFG)
    assert current_form(a["meta"]) == "ref"


def test_non_stale_tool_unaffected():
    a = _item("a", 1, tool="run_command", path="/f.txt")
    b = _item("b", 5, tool="run_command", path="/f.txt")
    ce.apply_supersession([a, b], cfg=CFG)
    assert current_form(a["meta"]) == "content"

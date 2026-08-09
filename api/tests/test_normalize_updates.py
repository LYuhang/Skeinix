"""The VIBE_ACTION normalizer is now a pass-through (the frontend refetches and
never applies ops, so no v1 lowering). The `updates` key + array shape are the
load-bearing contract (frontend toast reads payload.updates.length)."""
from vibecanvas_api.agent import _normalize_updates_for_frontend


def test_passthrough_preserves_v2_ops():
    ops = [
        ["replace", "/node_1/node_config/temperature", 0.2],
        ["add", "/node_2/children/-", "node_3"],
        ["remove", "/node_4"],
        ["text_edit", "/node_1/node_config/prompt_template", [["replace", "a", "b"]]],
    ]
    out = _normalize_updates_for_frontend(ops, {})
    assert out == ops


def test_empty_is_empty():
    assert _normalize_updates_for_frontend([], {}) == []
    assert _normalize_updates_for_frontend(None, {}) == []

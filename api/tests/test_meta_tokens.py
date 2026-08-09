"""Per-message metadata token bookkeeping."""
from vibecanvas_api.agents.middleware.meta_tokens import (
    new_meta, stamp_tokens, set_current_form, current_tokens, context_size, FORMS,
)


def test_new_meta_shape():
    m = new_meta("msg_1", round_index=0)
    assert m["unique_id"] == "msg_1"
    assert m["round_index"] == 0
    assert m["tokens"]["current_form"] == "content"
    for f in FORMS:
        assert f in m["tokens"]


def test_stamp_tokens_write_once_frozen():
    m = new_meta("m", 0)
    stamp_tokens(m, "content", 100)
    assert m["tokens"]["content"] == 100
    # frozen-once: a second stamp for the same form is a no-op
    stamp_tokens(m, "content", 999)
    assert m["tokens"]["content"] == 100


def test_current_tokens_follows_current_form():
    m = new_meta("m", 0)
    stamp_tokens(m, "content", 100)
    stamp_tokens(m, "content_abstract", 5)
    assert current_tokens(m) == 100          # current_form defaults to content
    set_current_form(m, "content_abstract")
    assert current_tokens(m) == 5


def test_context_size_sums_current_forms():
    a = new_meta("a", 0); stamp_tokens(a, "content", 100)
    b = new_meta("b", 1); stamp_tokens(b, "content", 200)
    set_current_form(a, "content_abstract"); stamp_tokens(a, "content_abstract", 4)
    assert context_size([a, b]) == 4 + 200


def test_ref_is_a_valid_form():
    m = new_meta("m", 0)
    stamp_tokens(m, "ref", 12)
    set_current_form(m, "ref")
    assert current_tokens(m) == 12

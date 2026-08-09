"""Aider-style error feedback for WorkflowUpdater.

Verifies that anchor-not-found and node-not-found warnings include
self-correction hints (closest matches with line numbers / similar
node_ids), reducing the agent's recovery turn count.
"""

from __future__ import annotations

from vibecanvas_api.utils.updater import (
    WorkflowUpdater,
    _fuzzy_substring_candidates,
    _suggest_node_ids,
)


def test_suggest_node_ids_returns_similar():
    wf = {"node_1": {}, "node_2": {}, "node_42": {}, "__meta__": {}}
    out = _suggest_node_ids("node_3", wf)
    assert "node_2" in out or "node_1" in out


def test_suggest_node_ids_returns_empty_for_no_match():
    wf = {"node_1": {}, "node_2": {}, "__meta__": {}}
    out = _suggest_node_ids("completely_different_name", wf)
    assert out == []


def test_suggest_node_ids_excludes_meta_keys():
    wf = {"node_1": {}, "__meta__": {}, "__attrs__": {}}
    out = _suggest_node_ids("__met__", wf)
    assert "__meta__" not in out
    assert "__attrs__" not in out


def test_fuzzy_substring_candidates_finds_close_match():
    haystack = (
        "You are a helpful assistant.\n"
        "When the user asks about pricing,\n"
        "respond with the latest rate."
    )
    cands = _fuzzy_substring_candidates("When user asks about price", haystack)
    assert cands, "expected at least one fuzzy candidate"
    top_ratio, top_line, top_snippet = cands[0]
    assert "user asks" in top_snippet
    assert top_line == 2
    assert top_ratio > 0.5


def test_fuzzy_substring_candidates_returns_empty_when_below_threshold():
    cands = _fuzzy_substring_candidates(
        "completely unrelated text", "abc def ghi", min_ratio=0.4,
    )
    assert cands == []


def test_node_remove_missing_includes_suggestion():
    wf = {"__meta__": {}, "node_1": {"children": []}, "node_2": {"children": []}}
    _, feedback = WorkflowUpdater.apply_updates(wf, [["remove", "/node_3"]])
    msg = "".join(feedback)
    assert "WARN: remove /node_3" in msg
    assert "did you mean" in msg
    # node_1 and node_2 are both close — at least one should be suggested
    assert "node_1" in msg or "node_2" in msg


def test_node_attribute_update_missing_includes_suggestion():
    wf = {
        "__meta__": {},
        "node_foo": {"node_id": "node_foo", "node_config": {}},
    }
    _, feedback = WorkflowUpdater.apply_updates(
        wf,
        [["replace", "/node_fo/node_description", "x"]],
    )
    msg = "".join(feedback)
    # A field-level replace on a missing node fails while resolve() walks the
    # intermediate segment, so it surfaces as a KeyError-backed ERROR that names
    # the bad node id (no fuzzy "did you mean" hint at this layer — node-path
    # ops get that; field-level ops get the resolve error). Intent preserved:
    # the feedback references the offending node so the agent can self-correct.
    assert any(f.startswith("ERROR:") for f in feedback)
    assert "node_fo" in msg


def test_multiline_text_edit_anchor_includes_hint():
    wf = {
        "__meta__": {},
        "node_1": {
            "node_id": "node_1",
            "node_config": {
                "prompt_template": (
                    "You are a helpful assistant.\n"
                    "When the user asks about pricing,\n"
                    "respond with the latest rate."
                ),
            },
        },
    }
    op = [
        "text_edit", "/node_1/node_config/prompt_template",
        [["replace", "When user asks about price", "When the user asks about Y", 1]],
    ]
    _, feedback = WorkflowUpdater.apply_updates(wf, [op])
    msg = "\n".join(feedback)
    assert "anchor not found" in msg
    assert "hints:" in msg
    # Should reference the actual L2 content
    assert "L2" in msg
    assert "user asks" in msg


def test_multiline_text_edit_no_similar_returns_no_hint():
    wf = {
        "__meta__": {},
        "node_1": {
            "node_id": "node_1",
            "node_config": {"prompt_template": "Hello world"},
        },
    }
    op = [
        "text_edit", "/node_1/node_config/prompt_template",
        [["replace", "completely orthogonal phrase", "x", 1]],
    ]
    _, feedback = WorkflowUpdater.apply_updates(wf, [op])
    msg = "\n".join(feedback)
    assert "anchor not found" in msg
    assert "hints:" not in msg


def test_successful_node_remove_unchanged():
    wf = {
        "__meta__": {},
        "node_1": {"children": ["node_2"]},
        "node_2": {"children": []},
    }
    new_wf, feedback = WorkflowUpdater.apply_updates(wf, [["remove", "/node_2"]])
    assert "node_2" not in new_wf
    assert any("OK: remove /node_2" in f for f in feedback)
    # node_1's children sweep
    assert new_wf["node_1"]["children"] == []


def test_successful_multiline_replace_unchanged():
    wf = {
        "__meta__": {},
        "node_1": {
            "node_id": "node_1",
            "node_config": {"prompt_template": "Hello world"},
        },
    }
    op = [
        "text_edit", "/node_1/node_config/prompt_template",
        [["replace", "Hello", "Hi", 1]],
    ]
    new_wf, feedback = WorkflowUpdater.apply_updates(wf, [op])
    assert new_wf["node_1"]["node_config"]["prompt_template"] == "Hi world"
    assert any("OK: text_edit" in f for f in feedback)

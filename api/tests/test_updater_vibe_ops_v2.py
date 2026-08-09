"""apply_updates v2 — JSON-Patch ops (add/replace/remove/text_edit).

In-memory dict workflows; no DB. A 'node' is a top-level dict key matching
^node_\\d+$ (plus the reserved __meta__). `add`/`remove` at a 1-segment pointer
is a whole-node op; deeper pointers are field-level. `remove` of a node sweeps
dangling children edges (children-only, same as v1).
"""
from vibecanvas_api.utils.updater import WorkflowUpdater


def _wf():
    return {
        "node_1": {"node_id": "node_1", "node_type": "StartNode",
                   "node_config": {"temperature": 0.7}, "children": ["node_2"]},
        "node_2": {"node_id": "node_2", "node_type": "EndNode",
                   "node_config": {}, "children": []},
        "__meta__": {"version": 1},
    }


def test_replace_field():
    wf, fb = WorkflowUpdater.apply_updates(
        _wf(), [["replace", "/node_1/node_config/temperature", 0.2]])
    assert wf["node_1"]["node_config"]["temperature"] == 0.2
    assert any(f.startswith("OK: replace") for f in fb)


def test_replace_missing_target_errors():
    _wf2, fb = WorkflowUpdater.apply_updates(
        _wf(), [["replace", "/node_1/node_config/missing", 1]])
    assert any(f.startswith("ERROR:") for f in fb)


def test_add_dict_key():
    wf, _fb = WorkflowUpdater.apply_updates(
        _wf(), [["add", "/node_1/node_config/top_p", 0.9]])
    assert wf["node_1"]["node_config"]["top_p"] == 0.9


def test_add_list_append_sentinel():
    wf, _fb = WorkflowUpdater.apply_updates(
        _wf(), [["add", "/node_2/children/-", "node_3"]])
    assert wf["node_2"]["children"] == ["node_3"]


def test_add_whole_node():
    new_node = {"node_id": "node_9", "node_type": "EndNode",
                "node_config": {}, "children": []}
    wf, _fb = WorkflowUpdater.apply_updates(_wf(), [["add", "/node_9", new_node]])
    assert wf["node_9"]["node_type"] == "EndNode"


def test_remove_field():
    wf, _fb = WorkflowUpdater.apply_updates(
        _wf(), [["remove", "/node_1/node_config/temperature"]])
    assert "temperature" not in wf["node_1"]["node_config"]


def test_remove_node_sweeps_children_edges():
    wf, fb = WorkflowUpdater.apply_updates(_wf(), [["remove", "/node_2"]])
    assert "node_2" not in wf
    assert wf["node_1"]["children"] == []
    assert any("node_2" in f and "remove" in f for f in fb)


def test_text_edit_replace_anchor():
    wf0 = _wf()
    wf0["node_1"]["node_config"]["prompt_template"] = "Hello world"
    wf, fb = WorkflowUpdater.apply_updates(
        wf0,
        [["text_edit", "/node_1/node_config/prompt_template",
          [["replace", "Hello", "Hi"]]]])
    assert wf["node_1"]["node_config"]["prompt_template"] == "Hi world"
    assert any(f.startswith("OK: text_edit") for f in fb)


def test_text_edit_anchor_not_found_warns():
    wf0 = _wf()
    wf0["node_1"]["node_config"]["prompt_template"] = "abc"
    _wf2, fb = WorkflowUpdater.apply_updates(
        wf0,
        [["text_edit", "/node_1/node_config/prompt_template",
          [["replace", "zzz", "q"]]]])
    assert any("not found" in f for f in fb)

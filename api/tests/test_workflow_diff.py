"""Unit tests for WorkflowUpdater.workflow_diff + diff_summary.

These power the agent's "what did vibe_workflow just change?" awareness:
the tool prepends the unified diff to its return content and a one-line
summary to the abstract. The end-to-end wire-up is exercised by the
existing route tests; this file focuses on the helpers' contract.
"""

from __future__ import annotations

from vibecanvas_api.utils.updater import WorkflowUpdater


def _wf(**nodes) -> dict:
    """Build a minimal workflow dict with __meta__ + given nodes."""
    return {"__meta__": {"workflow_id": "wf", "workflow_version": 1}, **nodes}


def test_diff_is_empty_for_equal_workflows():
    a = _wf(node_1={"node_id": "node_1", "node_config": {"k": "v"}})
    b = _wf(node_1={"node_id": "node_1", "node_config": {"k": "v"}})
    assert WorkflowUpdater.workflow_diff(a, b) == ""
    assert WorkflowUpdater.diff_summary(a, b) == "(no structural change)"


def test_diff_strips_meta_so_version_bump_is_silent():
    a = {"__meta__": {"workflow_subversion": 0}, "node_1": {"x": 1}}
    b = {"__meta__": {"workflow_subversion": 1}, "node_1": {"x": 1}}
    assert WorkflowUpdater.workflow_diff(a, b) == ""
    assert WorkflowUpdater.diff_summary(a, b) == "(no structural change)"


def test_diff_strips_node_attributes_so_canvas_drift_is_silent():
    a = _wf(node_1={"node_id": "node_1", "__attributes__": {"x": 0, "y": 0}})
    b = _wf(node_1={"node_id": "node_1", "__attributes__": {"x": 200, "y": 150}})
    assert WorkflowUpdater.workflow_diff(a, b) == ""
    assert WorkflowUpdater.diff_summary(a, b) == "(no structural change)"


def test_diff_shows_node_add():
    a = _wf()
    b = _wf(node_1={"node_id": "node_1", "node_type": "StartNode"})
    diff = WorkflowUpdater.workflow_diff(a, b)
    assert "+++ workflow_after" in diff
    assert "--- workflow_before" in diff
    assert "node_1" in diff
    assert WorkflowUpdater.diff_summary(a, b) == "+node_1"


def test_diff_shows_node_remove():
    a = _wf(node_1={"node_id": "node_1"})
    b = _wf()
    diff = WorkflowUpdater.workflow_diff(a, b)
    assert "node_1" in diff
    assert WorkflowUpdater.diff_summary(a, b) == "-node_1"


def test_diff_shows_node_modify():
    a = _wf(node_1={"node_id": "node_1", "node_config": {"prompt_template": "old text"}})
    b = _wf(node_1={"node_id": "node_1", "node_config": {"prompt_template": "new text"}})
    diff = WorkflowUpdater.workflow_diff(a, b)
    assert "old text" in diff
    assert "new text" in diff
    assert WorkflowUpdater.diff_summary(a, b) == "~node_1"


def test_diff_summary_combines_add_modify_remove():
    a = _wf(
        keep={"node_id": "keep", "v": 1},
        modify={"node_id": "modify", "v": 1},
        remove={"node_id": "remove"},
    )
    b = _wf(
        keep={"node_id": "keep", "v": 1},
        modify={"node_id": "modify", "v": 2},
        add={"node_id": "add"},
    )
    summary = WorkflowUpdater.diff_summary(a, b)
    assert "+add" in summary
    assert "~modify" in summary
    assert "-remove" in summary
    # keep should not appear
    assert "keep" not in summary
    # order: + before ~ before -
    plus_idx = summary.index("+add")
    tilde_idx = summary.index("~modify")
    dash_idx = summary.index("-remove")
    assert plus_idx < tilde_idx < dash_idx


def test_diff_truncates_when_too_long():
    # Build two workflows whose diff exceeds max_lines.
    a = _wf(**{f"node_{i}": {"v": i} for i in range(40)})
    b = _wf(**{f"node_{i}": {"v": i + 1} for i in range(40)})
    diff = WorkflowUpdater.workflow_diff(a, b, max_lines=20)
    lines = diff.splitlines()
    assert len(lines) <= 21  # 20 + truncation marker
    assert "more diff lines truncated" in diff


def test_diff_handles_empty_before():
    a = {}
    b = _wf(node_1={"node_id": "node_1"})
    diff = WorkflowUpdater.workflow_diff(a, b)
    assert "node_1" in diff
    assert WorkflowUpdater.diff_summary(a, b) == "+node_1"


def test_diff_prompt_template_change_visible_in_diff():
    """Direct support for the most common edit type: prompt_template
    modifications must be visible in the diff so the agent can self-check."""
    a = _wf(node_1={
        "node_id": "node_1",
        "node_config": {"prompt_template": "You are a helpful assistant."},
    })
    b = _wf(node_1={
        "node_id": "node_1",
        "node_config": {"prompt_template": "You are a friendly assistant."},
    })
    diff = WorkflowUpdater.workflow_diff(a, b)
    # JSON serialization preserves the changed string on adjacent diff lines
    assert "helpful" in diff
    assert "friendly" in diff

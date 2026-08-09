"""state.md wiring — normal file tools maintain state.md; StateEdit pins it."""


def test_update_state_removed_from_main_toolset():
    from vibecanvas_api.agents.tools import build_tools
    names = {getattr(t, "name", "") for t in build_tools(set())}
    assert "update_state" not in names
    assert {"read_file", "write_file", "edit_file"} <= names


def test_state_edit_in_context_edits_when_vfs_present():
    from vibecanvas_api.agent import _build_context_edits
    edits = _build_context_edits(vfs_store=object(), wf_id="wf1")
    types = [type(e).__name__ for e in edits]
    assert "StateEdit" in types
    # pinned/post-compaction: StateEdit appended AFTER LifecyclePolicyEdit so it
    # is not subject to S1/S2b compaction.
    assert types.index("StateEdit") > types.index("LifecyclePolicyEdit")


def test_state_edit_omitted_without_vfs():
    """No persistent VFS/wf_id → StateEdit cannot read state.md → omit it (inert),
    consistent with how S2a/S2b/VfsBodyReader stay inert absent vfs_store/wf_id."""
    from vibecanvas_api.agent import _build_context_edits
    edits = _build_context_edits()  # no vfs_store, no wf_id
    types = [type(e).__name__ for e in edits]
    assert "StateEdit" not in types

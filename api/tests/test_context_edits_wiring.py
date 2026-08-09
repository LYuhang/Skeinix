def test_context_edits_use_lifecycle_and_keep_prefix_strip():
    from vibecanvas_api.agent import _build_context_edits
    edits = _build_context_edits()
    types = [type(e).__name__ for e in edits]
    assert "LifecyclePolicyEdit" in types
    assert "ContextPrefixStripEdit" in types
    assert "ClearToolUsesEdit" not in types
    assert "WorkflowProjectionStripEdit" not in types


def test_middleware_compacts_a_copy_without_touching_original():
    import json
    from langchain_core.messages import ToolMessage
    from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
    big = json.dumps({"status": "success", "error": None, "abstract": "a",
                      "output": {"path": "/data/x.jsonl", "content_type": "table/jsonl",
                                 "data": "y" * 9000}}, ensure_ascii=False)
    original = [ToolMessage(content=big, tool_call_id="c", name="t") for _ in range(6)]
    work = [m.model_copy() for m in original]
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0).apply(
        work, count_tokens=lambda ms: sum(len(m.content) for m in ms) // 4)
    assert any("data" not in json.loads(m.content).get("output", {}) for m in work[:3])
    assert all("data" in json.loads(m.content)["output"] for m in original)  # original intact

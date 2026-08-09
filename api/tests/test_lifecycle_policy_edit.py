import json
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit


def _tool(content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="c", name="t")


def _env(path: str, ct: str, big: int = 0) -> str:
    return json.dumps({"status": "success", "error": None, "abstract": f"out {path}",
                       "output": {"path": path, "content_type": ct, "data": "x" * big}},
                      ensure_ascii=False)


def _toklen(messages) -> int:
    return sum(len(getattr(m, "content", "") or "") for m in messages) // 4


def test_below_trigger_is_noop():
    msgs = [_tool(_env("/data/a.jsonl", "table/jsonl"))]
    before = [m.content for m in msgs]
    LifecyclePolicyEdit(trigger=10_000, clear_at_least=0).apply(msgs, count_tokens=_toklen)
    assert [m.content for m in msgs] == before


def test_over_trigger_degrades_oldest_data_to_reference_keeps_recent_full():
    msgs = [_tool(_env(f"/data/q{i}.jsonl", "table/jsonl", big=8000)) for i in range(6)]
    LifecyclePolicyEdit(trigger=1_000, clear_at_least=0).apply(msgs, count_tokens=_toklen)
    assert all('"data"' in msgs[i].content for i in (3, 4, 5))  # fresh_k=3 most-recent stay full
    for i in (0, 1, 2):
        obj = json.loads(msgs[i].content)
        assert "data" not in obj["output"] and obj["output"]["path"] == f"/data/q{i}.jsonl"


def test_interactive_artifact_protection_is_counted_by_human_turn():
    interactive = "application/vnd.vibecanvas.interactive-artifact+json"
    msgs = [
        HumanMessage(content="first"),
        _tool(_env("/memory/interactive/old-1.json", interactive, big=8000)),
        _tool(_env("/memory/interactive/old-2.json", interactive, big=8000)),
        HumanMessage(content="latest"),
        _tool(_env("/memory/interactive/new-1.json", interactive, big=8000)),
        _tool(_env("/memory/interactive/new-2.json", interactive, big=8000)),
        _tool(_env("/memory/interactive/new-3.json", interactive, big=8000)),
    ]

    LifecyclePolicyEdit(
        trigger=1,
        clear_at_least=0,
        interactive_artifact_protect_recent_rounds=0,
    ).apply(msgs, count_tokens=_toklen)

    assert '"data"' not in msgs[1].content
    assert '"data"' not in msgs[2].content
    assert all('"data"' in msgs[i].content for i in (4, 5, 6))


def _tool_artifact(tool: str, **handles) -> dict:
    return {
        "schema_version": 1,
        "status": "success",
        "artifact": {"kind": "tool_result", "handles": handles},
        "meta": {"tool": tool},
    }


def _named_tool(name: str, content: str, **handles) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=f"{name}:{len(content)}",
        name=name,
        artifact=_tool_artifact(name, **handles),
    )


def _artifact(tool: str, status: str = "success", **handles) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "artifact": {"kind": "tool_result", "handles": handles},
        "meta": {"tool": tool, "content_type": "text/plain"},
    }


def test_successful_write_input_is_abbreviated_but_failure_keeps_full_input():
    long_text = "A" * 10_000 + "\nMIDDLE\n" + "Z" * 10_000
    ok_ai = AIMessage(content="", tool_calls=[{
        "id": "ok1", "name": "write_file", "args": {"path": "/data/a.txt", "content": long_text},
    }])
    fail_ai = AIMessage(content="", tool_calls=[{
        "id": "bad1", "name": "write_file", "args": {"path": "/data/b.txt", "content": long_text},
    }])
    msgs = [
        HumanMessage(content="write"),
        ok_ai,
        ToolMessage(content="File written: /data/a.txt", name="write_file",
                    tool_call_id="ok1", artifact=_artifact("write_file", path="/data/a.txt")),
        fail_ai,
        ToolMessage(content="could not write '/data/b.txt'", name="write_file",
                    tool_call_id="bad1",
                    artifact=_artifact("write_file", status="error", path="/data/b.txt")),
    ]

    LifecyclePolicyEdit(
        trigger=1_000_000,
        file_input_head_tokens=16,
        file_input_tail_tokens=16,
    ).apply(msgs, count_tokens=_toklen)

    compacted = msgs[1].tool_calls[0]["args"]["content"]
    assert compacted.startswith("A")
    assert compacted.endswith("Z")
    assert "tokens omitted to save context" in compacted
    assert "full at" not in compacted
    assert "read_file" not in compacted
    assert "abbreviated after a successful tool call" not in compacted
    assert msgs[3].tool_calls[0]["args"]["content"] == long_text


def test_file_output_policy_ages_read_write_edit_outputs():
    body = "line\n" * 200
    msgs = [
        HumanMessage(content="read"),
        ToolMessage(content=body, name="read_file", tool_call_id="r1",
                    artifact=_artifact("read_file", path="/data/a.txt")),
        HumanMessage(content="next"),
    ]

    LifecyclePolicyEdit(
        trigger=1_000_000,
        file_context_tiers=[{"max_tokens": None, "full_rounds": 0}],
        file_context_head_tokens=8,
        file_context_tail_tokens=8,
    ).apply(msgs, count_tokens=_toklen)

    assert "read_file output abbreviated by file context policy" in msgs[1].content
    assert "/data/a.txt" in msgs[1].content
    assert "tokens elided" in msgs[1].content or "lines elided" in msgs[1].content


def test_lifecycle_records_current_form_projection_by_message_id():
    holder = {}
    msgs = [
        HumanMessage(content="read"),
        ToolMessage(
            id="tool-msg-1",
            content="line\n" * 200,
            name="read_file",
            tool_call_id="r1",
            artifact=_artifact("read_file", path="/data/a.txt"),
        ),
        HumanMessage(content="next"),
    ]

    LifecyclePolicyEdit(
        trigger=1_000_000,
        file_context_tiers=[{"max_tokens": None, "full_rounds": 0}],
        file_context_head_tokens=8,
        file_context_tail_tokens=8,
        form_projection_holder=holder,
    ).apply(msgs, count_tokens=_toklen)

    assert holder["tool-msg-1"]["current_form"] == "head_tail"
    assert holder["tool-msg-1"]["token_field"] == "head_tail"
    assert holder["tool-msg-1"]["tool"] == "read_file"
    assert holder["tool-msg-1"]["path"] == "/data/a.txt"


def test_get_config_keeps_latest_per_scope():
    msgs = [
        _named_tool("get_config", '{"scope":"global","v":1}', scope="global"),
        _named_tool("get_config", '{"scope":"chat","v":1}', scope="chat"),
        _named_tool("get_config", '{"scope":"global","v":2}', scope="global"),
    ]

    LifecyclePolicyEdit(trigger=10_000).apply(msgs, count_tokens=_toklen)

    assert "superseded" in msgs[0].content
    assert '"scope":"chat"' in msgs[1].content
    assert '"v":2' in msgs[2].content


def test_get_node_spec_keeps_latest_per_node_type():
    msgs = [
        _named_tool("get_node_spec", "old prompt spec", node_type="PromptNode"),
        _named_tool("get_node_spec", "code spec", node_type="CodeNode"),
        _named_tool("get_node_spec", "new prompt spec", node_type="PromptNode"),
    ]

    LifecyclePolicyEdit(trigger=10_000).apply(msgs, count_tokens=_toklen)

    assert "superseded" in msgs[0].content
    assert msgs[1].content == "code spec"
    assert msgs[2].content == "new prompt spec"


def test_get_node_spec_caps_distinct_node_types_from_newest():
    msgs = [
        _named_tool("get_node_spec", "start spec", node_type="StartNode"),
        _named_tool("get_node_spec", "code old", node_type="CodeNode"),
        _named_tool("get_node_spec", "prompt spec", node_type="PromptNode"),
        _named_tool("get_node_spec", "condition spec", node_type="ConditionNode"),
        _named_tool("get_node_spec", "code new", node_type="CodeNode"),
        _named_tool("get_node_spec", "end spec", node_type="EndNode"),
    ]

    LifecyclePolicyEdit(trigger=10_000, max_node_specs=3).apply(msgs, count_tokens=_toklen)

    assert "superseded" in msgs[0].content
    assert "superseded" in msgs[1].content
    assert "superseded" in msgs[2].content
    assert msgs[3].content == "condition spec"
    assert msgs[4].content == "code new"
    assert msgs[5].content == "end spec"


def test_workflow_context_tools_share_current_workflow_keep_latest():
    msgs = [
        _named_tool("create_workflow", "created wf_1", workflow_id="wf_1"),
        _named_tool("set_workflow", "selected wf_2", workflow_id="wf_2"),
    ]

    LifecyclePolicyEdit(trigger=10_000).apply(msgs, count_tokens=_toklen)

    assert "superseded" in msgs[0].content
    assert "selected wf_2" in msgs[1].content


def test_execution_tools_keep_latest_by_semantic_key():
    msgs = [
        _named_tool("node_execute", "old node_1", workflow_id="wf_1", node_id="node_1"),
        _named_tool("node_execute", "node_2", workflow_id="wf_1", node_id="node_2"),
        _named_tool("node_execute", "new node_1", workflow_id="wf_1", node_id="node_1"),
        _named_tool("run_workflow", "old run", workflow_id="wf_1"),
        _named_tool("run_workflow", "new run", workflow_id="wf_1"),
    ]

    LifecyclePolicyEdit(trigger=10_000).apply(msgs, count_tokens=_toklen)

    assert "superseded" in msgs[0].content
    assert msgs[1].content == "node_2"
    assert msgs[2].content == "new node_1"
    assert "superseded" in msgs[3].content
    assert msgs[4].content == "new run"


def test_idempotent_within_call_and_deterministic():
    a = [_tool(_env(f"/data/q{i}.jsonl", "table/jsonl", big=8000)) for i in range(6)]
    b = [_tool(_env(f"/data/q{i}.jsonl", "table/jsonl", big=8000)) for i in range(6)]
    LifecyclePolicyEdit(trigger=1_000).apply(a, count_tokens=_toklen)
    LifecyclePolicyEdit(trigger=1_000).apply(b, count_tokens=_toklen)
    assert [m.content for m in a] == [m.content for m in b]
    snap = [m.content for m in a]
    LifecyclePolicyEdit(trigger=1_000).apply(a, count_tokens=_toklen)
    assert [m.content for m in a] == snap  # re-apply: already-cleared skipped


def test_shell_uses_head_tail():
    body = "\n".join(f"l{i}" for i in range(200))
    msgs = [_tool(json.dumps({"status": "success", "output": {
                "path": "/exec/cmd_0.log", "content_type": "text/shell", "data": body}})),
            _tool(_env("/exec/cmd_1.log", "text/shell"))]
    LifecyclePolicyEdit(trigger=1, clear_at_least=0).apply(msgs, count_tokens=_toklen)
    assert "lines elided" in msgs[0].content  # oldest shell → head_tail (the head_tail marker)


def test_non_envelope_output_is_not_total_loss():
    # A {"status":"error",...} return with NO output key is not an envelope; it
    # must NOT degrade to a bare "[output elided]" — its status/error must survive.
    err = json.dumps({"status": "error", "error": "permission denied for node_3"})
    msgs = [_tool(err), _tool(_env("/data/q.jsonl", "table/jsonl", big=20000))]
    LifecyclePolicyEdit(trigger=1, clear_at_least=0).apply(msgs, count_tokens=_toklen)
    assert "permission denied" in msgs[0].content  # short non-envelope kept whole


def test_all_protected_but_over_trigger_terminates_cleanly():
    # 2 data outputs, fresh_k(table/jsonl)=3 → both protected; over trigger but
    # nothing degradable → must terminate (no hang) leaving both full.
    msgs = [_tool(_env(f"/data/q{i}.jsonl", "table/jsonl", big=8000)) for i in range(2)]
    before = [m.content for m in msgs]
    LifecyclePolicyEdit(trigger=1, clear_at_least=0).apply(msgs, count_tokens=_toklen)
    assert [m.content for m in msgs] == before


def test_emits_compaction_structlog_event(monkeypatch):
    import json
    from langchain_core.messages import ToolMessage
    from vibecanvas_api.agents.middleware import lifecycle_policy as lp
    events = []
    class _Log:
        def info(self, ev, **kw): events.append((ev, kw))
    monkeypatch.setattr(lp, "_slog", _Log())
    msgs = [ToolMessage(content=json.dumps({"status": "success", "error": None, "abstract": "a",
            "output": {"path": f"/data/q{i}.jsonl", "content_type": "table/jsonl",
                       "data": "x" * 8000}}), tool_call_id="c", name="t") for i in range(6)]
    lp.LifecyclePolicyEdit(trigger=1_000, clear_at_least=0).apply(
        msgs, count_tokens=lambda ms: sum(len(m.content) for m in ms) // 4)
    assert any(ev == "context_compaction" for ev, _ in events)
    _, kw = next(e for e in events if e[0] == "context_compaction")
    assert "tokens_before" in kw and "degraded" in kw


def test_logging_failure_is_fail_soft(monkeypatch):
    import json
    from langchain_core.messages import ToolMessage
    from vibecanvas_api.agents.middleware import lifecycle_policy as lp
    class _Boom:
        def info(self, *a, **k): raise RuntimeError("log down")
    monkeypatch.setattr(lp, "_slog", _Boom())
    msgs = [ToolMessage(content=json.dumps({"status": "success", "error": None, "abstract": "a",
            "output": {"path": f"/d/q{i}.jsonl", "content_type": "table/jsonl", "data": "x"*8000}}),
            tool_call_id="c", name="t") for i in range(6)]
    lp.LifecyclePolicyEdit(trigger=1_000).apply(  # must NOT raise
        msgs, count_tokens=lambda ms: sum(len(m.content) for m in ms) // 4)


def test_no_event_when_nothing_degraded(monkeypatch):
    import json
    from langchain_core.messages import ToolMessage
    from vibecanvas_api.agents.middleware import lifecycle_policy as lp
    events = []
    class _Log:
        def info(self, ev, **kw): events.append(ev)
    monkeypatch.setattr(lp, "_slog", _Log())
    msgs = [ToolMessage(content=json.dumps({"status": "success", "error": None, "abstract": "a",
            "output": {"path": "/data/q.jsonl", "content_type": "table/jsonl", "data": "x"}}),
            tool_call_id="c", name="t")]
    lp.LifecyclePolicyEdit(trigger=10_000).apply(msgs, count_tokens=lambda ms: 1)  # under trigger
    assert "context_compaction" not in events

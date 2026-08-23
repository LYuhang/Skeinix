from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.middleware.hard_context_edit import HardContextEdit


def test_hard_context_skips_empty_chat_state():
    messages = [HumanMessage(content="hi")]
    ctx = SimpleNamespace(
        surface="chat",
        wf_id="__chatws_user_chat",
        current_workflow_id=None,
        active_commands=[],
        available_commands=["workflow"],
        loaded_mcp_names=[],
        todo_items=[],
    )

    HardContextEdit(ctx).apply(messages, count_tokens=lambda _: 0)

    assert [m.content for m in messages] == ["hi"]


def test_hard_context_skips_workflow_command_and_loaded_integrations_without_todos():
    messages = [HumanMessage(content="hi")]
    ctx = SimpleNamespace(
        surface="chat",
        wf_id="__chatws_user_chat",
        current_workflow_id="wf_123",
        active_commands=["workflow"],
        available_commands=["workflow"],
        loaded_mcp_names=["filesystem"],
        todo_items=[],
    )

    HardContextEdit(ctx).apply(messages, count_tokens=lambda _: 0)

    assert [m.content for m in messages] == ["hi"]


def test_hard_context_injects_unfinished_todos_before_latest_ai_message():
    messages = [
        HumanMessage(content="build this"),
        AIMessage(content="", tool_calls=[{
            "id": "call_1",
            "name": "write_file",
            "args": {"path": "/data/a.txt"},
        }]),
        ToolMessage(content="ok", tool_call_id="call_1"),
        AIMessage(content="I wrote the file."),
    ]
    ctx = SimpleNamespace(
        surface="chat",
        wf_id="__chatws_user_chat",
        current_workflow_id="wf_123",
        active_commands=["workflow"],
        available_commands=["workflow"],
        loaded_mcp_names=["filesystem"],
        todo_items=[{"id": 1, "text": "Check workflow", "status": "pending"}],
    )

    HardContextEdit(ctx).apply(messages, count_tokens=lambda _: 0)

    assert [type(m).__name__ for m in messages] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "HumanMessage",
        "AIMessage",
    ]
    assert "<todo-reminder>" in messages[3].content
    assert "Check workflow" in messages[3].content
    assert getattr(messages[2], "tool_call_id") == "call_1"


def test_hard_context_injects_unfinished_todos_at_tail_when_no_ai_exists():
    messages = [HumanMessage(content="hi")]
    ctx = SimpleNamespace(
        surface="chat",
        wf_id="__chatws_user_chat",
        current_workflow_id="wf_123",
        active_commands=["workflow"],
        available_commands=["workflow"],
        loaded_mcp_names=[],
        todo_items=[{"id": 1, "text": "Check workflow", "status": "pending"}],
    )

    HardContextEdit(ctx).apply(messages, count_tokens=lambda _: 0)

    assert [type(m).__name__ for m in messages] == ["HumanMessage", "HumanMessage"]
    assert "<todo-reminder>" in messages[-1].content


def test_hard_context_replaces_old_reminder_even_when_new_state_empty():
    messages = [
        HumanMessage(content="hi"),
        HumanMessage(content="<system-reminder>\n<hard-context>\ncurrent_workflow_id: wf_old\n</hard-context>\n</system-reminder>"),
        HumanMessage(content="<system-reminder>\n<todo-reminder>\nold todo\n</todo-reminder>\n</system-reminder>"),
    ]
    ctx = SimpleNamespace(
        surface="chat",
        wf_id="__chatws_user_chat",
        current_workflow_id=None,
        active_commands=[],
        available_commands=["workflow"],
        loaded_mcp_names=[],
        todo_items=[],
    )

    HardContextEdit(ctx).apply(messages, count_tokens=lambda _: 0)

    assert [m.content for m in messages] == ["hi"]


def test_hard_context_accepts_context_holder_dict():
    messages = [HumanMessage(content="hi"), AIMessage(content="next")]
    ctx = SimpleNamespace(
        surface="chat",
        wf_id="__chatws_user_chat",
        current_workflow_id=None,
        active_commands=[],
        available_commands=["workflow"],
        loaded_mcp_names=[],
        todo_items=[{"id": 7, "text": "Update status", "status": "in_progress"}],
    )

    HardContextEdit({"context": ctx}).apply(messages, count_tokens=lambda _: 0)

    assert "<todo-reminder>" in messages[1].content
    assert "Update status" in messages[1].content
    assert messages[2].content == "next"

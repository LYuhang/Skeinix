import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from vibecanvas_api.agents.middleware import debug_snapshot_edit as debug_module
from vibecanvas_api.agents.middleware.debug_snapshot_edit import DebugSnapshotEdit


class FakeVfs:
    def __init__(self):
        self.writes = []

    def upsert_internal_artifact(self, **kwargs):
        self.writes.append(kwargs)
        return False


@pytest.mark.asyncio
async def test_debug_snapshot_edit_writes_model_input_snapshot():
    vfs = FakeVfs()
    ctx = SimpleNamespace(
        tenant_id="tenant-1",
        wf_id="__chatws_user_chat",
        chat_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
    )
    cfg = SimpleNamespace(
        model="openai:gpt-test",
        compaction_v2=SimpleNamespace(window_tokens=200000, v2_enabled=True),
    )
    edit = DebugSnapshotEdit(context={"context": ctx}, vfs=vfs, agent_cfg=cfg)
    messages = [
        HumanMessage(id="user-msg-1", content="hello"),
        ToolMessage(
            id="tool-msg-1",
            content="head\n...\ntail",
            name="read_file",
            tool_call_id="call-1",
            response_metadata={
                "tokens": {
                    "form": "head_tail",
                    "raw": 154_709,
                    "head_tail": 4_026,
                    "abstract": 13,
                    "compressed": None,
                }
            },
        ),
        HumanMessage(
            content="<system-reminder>\n<hard-context>\ncurrent_workflow_id: wf1\n</hard-context>\n</system-reminder>"
        ),
    ]

    edit.apply(messages, count_tokens=lambda xs: 1)
    assert len(messages) == 3
    assert edit._tasks
    await edit._tasks[0]

    assert len(vfs.writes) == 1
    write = vfs.writes[0]
    assert write["wf_id"] == "__chatws_user_chat"
    assert write["path"].startswith("/logs/.debug/")
    assert write["path"].endswith(".json")
    payload = json.loads(write["content"])
    assert payload["kind"] == "agent_model_input_snapshot"
    assert payload["runtime_type"] == "langchain"
    assert payload["snapshot_semantics"] == "model_input"
    assert payload["chat_id"] == "chat-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["turn_id"] == "turn-1"
    assert payload["model_call_index"] == 1
    assert payload["target"]["provider"] == "openai"
    assert payload["target"]["model_id"] == "openai:gpt-test"
    assert payload["target"]["context_window_tokens"] == 200000
    assert payload["token_total"] == 4_028
    assert payload["messages"][0]["source_message_id"] == "user-msg-1"
    assert payload["messages"][1]["form"] == "preview"
    assert payload["messages"][1]["preview_strategy"] == "head_tail"
    assert payload["messages"][1]["token_field"] == "preview"
    assert payload["messages"][1]["tokens"] == 4_026
    assert payload["messages"][1]["token_slots"]["raw"] == 154_709
    assert payload["messages"][1]["token_slots"]["preview"] == 4_026
    assert payload["messages"][2]["synthetic"] is True
    assert payload["messages"][2]["synthetic_kind"] == "hard_context"
    assert payload["messages"][2]["anchor_source_message_id"] == "tool-msg-1"


@pytest.mark.asyncio
async def test_debug_snapshot_writes_workspace_logs_inside_agent_sandbox(
    monkeypatch, tmp_path
):
    debug_dir = tmp_path / "logs" / ".debug"
    monkeypatch.setattr(debug_module, "DEBUG_DIR", str(debug_dir))
    monkeypatch.setenv("VIBECANVAS_AGENT_RUNTIME_IN_SANDBOX", "1")
    vfs = FakeVfs()
    ctx = SimpleNamespace(
        tenant_id="tenant-1",
        wf_id="__chatws_user_chat",
        chat_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
    )
    cfg = SimpleNamespace(model="openai:gpt-test", compaction_v2=None)
    edit = DebugSnapshotEdit(context={"context": ctx}, vfs=vfs, agent_cfg=cfg)

    edit.apply([HumanMessage(content="hello")], count_tokens=lambda _xs: 1)
    await edit._tasks[0]

    assert vfs.writes == []
    files = list(debug_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "agent_model_input_snapshot"
    assert payload["turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_debug_snapshot_reads_target_from_runtime_dict_config():
    vfs = FakeVfs()
    ctx = SimpleNamespace(
        tenant_id="tenant-1",
        wf_id="__chatws_user_chat",
        chat_id="chat-1",
        thread_id="thread-1",
        turn_id="turn-1",
    )
    edit = DebugSnapshotEdit(
        context={"context": ctx},
        vfs=vfs,
        agent_cfg={
            "model": "openai:ep-test",
            "model_context_tokens": 256000,
            "compaction_v2": {"v2_enabled": True, "window_tokens": 128000},
        },
    )

    edit.apply([HumanMessage(content="hello")], count_tokens=lambda _xs: 1)
    await edit._tasks[0]

    payload = json.loads(vfs.writes[0]["content"])
    assert payload["target"] == {
        "provider": "openai",
        "model_id": "openai:ep-test",
        "context_window_tokens": 256000,
    }
    assert payload["memory_config_snapshot"]["compaction_v2_enabled"] is True

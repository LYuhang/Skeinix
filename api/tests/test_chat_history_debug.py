import json

from langchain_core.messages import ToolMessage, HumanMessage

from vibecanvas_api.routes.chats import _debug_meta


def test_debug_meta_for_tool_message():
    env = json.dumps({"status": "success", "error": None, "abstract": "a",
                      "output": {"path": "/data/x.jsonl", "content_type": "table/jsonl"}})
    m = ToolMessage(content=env, tool_call_id="c", name="t",
                    response_metadata={"context_editing": {"cleared": True, "form": "reference"}})
    meta = _debug_meta(m)
    assert meta["role"] == "tool"
    assert meta["content_type"] == "table/jsonl"
    assert meta["path"] == "/data/x.jsonl"
    assert meta["frozen"] is True and meta["aged_form"] == "reference"
    assert meta["approx_tokens"] >= 0


def test_debug_meta_plain_human_message_has_no_tool_fields():
    meta = _debug_meta(HumanMessage(content="hello"))
    assert meta["role"] == "user"
    assert "content_type" not in meta and "frozen" not in meta

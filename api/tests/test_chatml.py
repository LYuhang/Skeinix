from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from vibecanvas_api.agent import _cancelled_tool_messages
from vibecanvas_api.agents.chatml import to_chatml_message, to_chatml, from_chatml
from vibecanvas_api.agents.prefix import build_file_attachment_prefix, strip_context_prefix


def test_tool_message_to_chatml():
    cm = to_chatml_message(ToolMessage(content="env", tool_call_id="c1", name="read_file"))
    assert cm == {"role": "tool", "content": "env", "tool_call_id": "c1", "name": "read_file"}


def test_ai_tool_calls_openai_shape():
    ai = AIMessage(content="hi", tool_calls=[{"id": "c1", "name": "f", "args": {"a": 1}}])
    cm = to_chatml_message(ai)
    assert cm["role"] == "assistant"
    assert cm["tool_calls"][0]["function"]["name"] == "f"


def test_cancelled_tool_message_chatml_shape():
    ai = AIMessage(content="", tool_calls=[{"id": "c1", "name": "bash", "args": {}}])
    [tool] = _cancelled_tool_messages(ai)
    cm = to_chatml_message(tool)
    assert cm["role"] == "tool"
    assert cm["tool_call_id"] == "c1"
    assert cm["name"] == "bash"
    assert cm["artifact"]["status"] == "error"
    assert cm["artifact"]["error"]["code"] == "user_cancelled"


def test_to_chatml_skips_system_and_from_chatml_roundtrips_roles():
    msgs = [SystemMessage(content="s"), HumanMessage(content="hello"),
            ToolMessage(content="o", tool_call_id="c", name="t")]
    cm = to_chatml(msgs)
    assert [m["role"] for m in cm] == ["user", "tool"]
    back = from_chatml([{"role": "user", "content": "hi"}])
    assert isinstance(back[0], HumanMessage)


def test_file_attachments_survive_checkpoint_projection_while_prefix_is_hidden():
    attachments = [{
        "type": "image",
        "name": "photo.png",
        "path": "/data/attachments/abc_photo.png",
        "content_type": "image/png",
        "size_bytes": 12,
    }]
    prefix = build_file_attachment_prefix(attachments)
    assert "/data/attachments/abc_photo.png" in prefix
    msg = HumanMessage(
        content=prefix + "describe this",
        additional_kwargs={"attachments": attachments},
    )
    projected = to_chatml_message(msg)
    assert projected["content"] == "describe this"
    assert projected["attachments"] == attachments
    assert strip_context_prefix(msg.content) == "describe this"

"""Metadata carriage through ChatML and round-trip conversion."""
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from vibecanvas_api.agents.chatml import to_chatml, from_chatml


def _meta(uid, r):
    return {"unique_id": uid, "round_index": r,
            "tokens": {"current_form": "content", "content": 3}}


def test_meta_carried_into_chatml_dict():
    m = HumanMessage(content="hi", additional_kwargs={"_meta": _meta("u1", 0)})
    d = to_chatml([m])
    assert d[0]["_meta"]["unique_id"] == "u1"


def test_roundtrip_preserves_meta_and_content():
    msgs = [
        HumanMessage(content="hello", additional_kwargs={"_meta": _meta("u1", 0)}),
        AIMessage(content="hi there", additional_kwargs={"_meta": _meta("a1", 0)}),
        ToolMessage(content="{}", tool_call_id="tc1", name="x",
                    additional_kwargs={"_meta": _meta("t1", 0)}),
    ]
    back = from_chatml(to_chatml(msgs))
    assert [b.content for b in back] == ["hello", "hi there", "{}"]
    assert [b.additional_kwargs.get("_meta", {}).get("unique_id") for b in back] == ["u1", "a1", "t1"]


def test_missing_meta_is_fine():
    back = from_chatml(to_chatml([HumanMessage(content="x")]))
    assert back[0].content == "x"
    assert "_meta" not in back[0].additional_kwargs

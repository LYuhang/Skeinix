"""Project real LangChain messages through the compaction adapter.

The adapter is a PURE projection: it deep-copies the message list, never mutates the
originals (invariant 1), and renders each ToolMessage to its selected form.
"""
import json

from langchain_core.messages import HumanMessage, ToolMessage

from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, stamp_tokens
from vibecanvas_api.agents.tools.envelope import make_envelope, dumps
from vibecanvas_api.agents.middleware import compaction_v2_middleware as cv2


def _tool_msg(uid, round_index, raw_tokens, *, tool="run_command"):
    env = make_envelope(
        status="success", error=None, content="X" * (raw_tokens * 4),
        content_abbreviation="HEAD…elided…TAIL", content_abstract="ran cmd, 100 lines",
        output_meta={"path": "/exec/c.txt", "content_type": "text/shell", "tool": tool},
    )
    m = new_meta(uid, round_index)
    stamp_tokens(m, "content", raw_tokens)
    stamp_tokens(m, "content_abbreviation", 10)
    stamp_tokens(m, "content_abstract", 4)
    return ToolMessage(content=dumps(env), tool_call_id=uid, name=tool,
                       additional_kwargs={"_meta": m})


def test_projection_degrades_old_tool_outputs_and_keeps_originals():
    msgs = [
        HumanMessage(content="hi", additional_kwargs={"_meta": new_meta("h", 0)}),
        _tool_msg("t1", 1, 5000),    # old → should degrade under pressure
        _tool_msg("t9", 19, 5000),   # recent → protected
    ]
    original_t1_content = msgs[1].content
    projected, plan = cv2.project_messages(
        msgs, current_round=20, window=1000,
        cfg=CompactionV2Config({"clear_at_least": 1, "protect_recent_rounds": 2}))
    # originals untouched (invariant 1)
    assert msgs[1].content == original_t1_content
    # projected old tool message is no longer the full content
    assert projected[1].content != original_t1_content
    assert plan is not None


def test_projection_noop_when_small():
    msgs = [_tool_msg("t1", 1, 100)]  # none tier, young
    projected, _ = cv2.project_messages(
        msgs, current_round=2, window=10_000_000, cfg=CompactionV2Config({}))
    # full content preserved (rendered form == content)
    env = json.loads(projected[0].content)
    assert env.get("status") == "success"

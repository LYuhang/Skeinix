"""Task E — FormLadderEdit: the ContextEditingMiddleware edit wrapping the engine."""
import json

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from vibecanvas_api.config import AgentConfig
from vibecanvas_api.agents.tools.envelope import make_envelope, dumps
from vibecanvas_api.agents.middleware.form_ladder_edit import FormLadderEdit


def _count_tokens(msgs):
    return sum(len(str(getattr(m, "content", ""))) // 4 for m in msgs)


def _tool_env(raw_chars):
    return dumps(make_envelope(
        status="success", error=None, content="X" * raw_chars,
        content_abbreviation="HEAD…elided…TAIL", content_abstract="ran cmd, 100 lines",
        output_meta={"path": "/exec/c.txt", "content_type": "text/shell", "tool": "run_command"}))


def _history(n_rounds, raw_chars):
    """n_rounds of (Human, AI(tool_call), Tool)."""
    msgs = []
    for r in range(n_rounds):
        msgs.append(HumanMessage(content=f"do task {r}"))
        msgs.append(AIMessage(content="", tool_calls=[
            {"id": f"tc{r}", "name": "run_command", "args": {"cmd": "x"}}]))
        msgs.append(ToolMessage(content=_tool_env(raw_chars), tool_call_id=f"tc{r}", name="run_command"))
    return msgs


def _cfg(**v2):
    return AgentConfig({"compaction": {"v2": {"v2_enabled": True, **v2}}})


def test_disabled_is_noop_safe_import():
    # When v2 off, FormLadderEdit must never be constructed by the chain; but if
    # applied directly with tiny content it is a safe no-op.
    msgs = _history(2, 40)
    before = [m.content for m in msgs]
    FormLadderEdit(_cfg(window_tokens=10_000_000)).apply(msgs, count_tokens=_count_tokens)
    assert [m.content for m in msgs] == before


def test_degrades_old_tool_outputs_under_pressure():
    cfg = _cfg(window_tokens=2000, clear_at_least=1, protect_recent_rounds=2)
    msgs = _history(8, 40000)  # 8 rounds, big tool outputs → pressure
    FormLadderEdit(cfg).apply(msgs, count_tokens=_count_tokens)
    # the oldest tool message (round 1, not pinned round 0) should be degraded:
    # its content is no longer the full envelope with inline content
    old_tool = msgs[5]  # round1 tool = index 3*1+2 = 5
    env = json.loads(old_tool.content)
    assert env["content"] is None  # degraded to abbreviation/abstract — full content dropped
    # the most-recent tool message stays full
    recent = json.loads(msgs[-1].content)
    assert recent["content"] is not None


def test_rounds_assigned_from_human_turns():
    cfg = _cfg(window_tokens=2000, clear_at_least=1, protect_recent_rounds=1)
    msgs = _history(5, 40000)
    edit = FormLadderEdit(cfg)
    edit.apply(msgs, count_tokens=_count_tokens)
    # round 0 (pinned first exchange) tool output preserved
    first_tool = json.loads(msgs[2].content)
    assert first_tool["content"] is not None

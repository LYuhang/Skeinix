"""Enforce bounded Diagram tool control flow at the Agent graph boundary."""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


_CONTINUE_ACTIONS = {"call_tool", "edit_source"}
_DIAGRAM_TOOLS = {
    "get_diagram_spec",
    "search_diagram_assets",
    "inspect_diagram",
    "check_diagram",
    "render_interactive",
    "review_diagram",
    "export_diagram",
}


def _tool_name(message: ToolMessage) -> str:
    if isinstance(message.name, str) and message.name:
        return message.name
    artifact = getattr(message, "artifact", None)
    meta = artifact.get("meta") if isinstance(artifact, dict) else None
    return str(meta.get("tool") or "") if isinstance(meta, dict) else ""


def _json_payload(message: ToolMessage) -> dict[str, Any] | None:
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        structured = artifact.get("structured_content")
        if isinstance(structured, dict):
            return structured
        if isinstance(artifact.get("next"), dict):
            return artifact
    content = message.content
    candidates: list[str] = []
    if isinstance(content, str):
        candidates.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                candidates.append(block["text"])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


class DiagramProtocolMiddleware(AgentMiddleware):
    """Prevent a model from silently abandoning a current-Turn Diagram chain."""

    def __init__(self, *, max_forced_continuations: int = 3) -> None:
        super().__init__()
        self.max_forced_continuations = max(1, int(max_forced_continuations))
        self.forced_continuations = 0
        self.current_turn_tool_call_ids: set[str] = set()
        self.current_turn_id: str | None = None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = getattr(runtime, "context", None)
        turn_id = str(getattr(context, "turn_id", "") or "")
        if turn_id and turn_id != self.current_turn_id:
            self.current_turn_id = turn_id
            self.forced_continuations = 0
            self.current_turn_tool_call_ids.clear()
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        latest = messages[-1]
        if latest.tool_calls:
            self.current_turn_tool_call_ids.update(
                str(call.get("id") or "")
                for call in latest.tool_calls
                if call.get("id")
            )
            return None
        latest_tool: ToolMessage | None = None
        for message in reversed(messages[:-1]):
            if not isinstance(message, ToolMessage):
                continue
            if str(message.tool_call_id or "") not in self.current_turn_tool_call_ids:
                continue
            if _tool_name(message) not in _DIAGRAM_TOOLS:
                continue
            latest_tool = message
            break
        if latest_tool is None:
            return None
        payload = _json_payload(latest_tool)
        next_step = payload.get("next") if isinstance(payload, dict) else None
        action = str(next_step.get("action") or "") if isinstance(next_step, dict) else ""
        if action not in _CONTINUE_ACTIONS:
            return None
        if self.forced_continuations >= self.max_forced_continuations:
            return None
        self.forced_continuations += 1
        tool_name = _tool_name(latest_tool)
        reminder = (
            "<system-reminder>\n"
            "Diagram protocol control: the latest current-turn "
            f"{tool_name} result returned next.action={action}. Do not finish "
            "or answer the user yet. Obey that next action using the exact "
            "returned refs/pointers. Continue the bounded Diagram chain; only "
            "a latest action=deliver or ask_user may complete it.\n"
            "</system-reminder>"
        )
        return {
            "messages": [HumanMessage(content=reminder)],
            "jump_to": "model",
        }

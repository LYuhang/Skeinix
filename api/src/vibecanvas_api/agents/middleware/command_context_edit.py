"""CommandContextEdit — persistent built-in command context projection.

Commands behave like platform-owned persistent skills. The original user message
is persisted in the checkpointer; this edit projects the full active command
context into the latest activation message for each command on every model call.
Earlier activations retain the user's text plus a compact superseded marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from vibecanvas_api.agents.commands import COMMAND_CONTEXT_HEADER
from vibecanvas_api.agents.token_accounting import (
    build_message_tokens,
    message_tokens,
)


COMMAND_CONTEXT_SUPERSEDED_HEADER = "<command-context-superseded"
_USER_MESSAGE_OPEN = "<user-message>\n"
_USER_MESSAGE_CLOSE = "\n</user-message>"


def _content(msg: Any) -> str:
    value = getattr(msg, "content", "")
    return value if isinstance(value, str) else ""


def _activation_name(msg: Any) -> str | None:
    ak = getattr(msg, "additional_kwargs", None) or {}
    data = ak.get("command_activation")
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        return data["name"]
    return None


def _source_content(msg: Any, command: str) -> str:
    """Return the unprojected user text when an edit is applied repeatedly."""
    content = _content(msg)
    ak = getattr(msg, "additional_kwargs", None) or {}
    command_context = ak.get("command_context")
    projection = (
        command_context.get(command)
        if isinstance(command_context, dict)
        else None
    )
    projected = (
        isinstance(projection, dict)
        and (
            projection.get("projection") in {"active", "superseded"}
            or projection.get("injected") is True
        )
    )
    if not projected and not (
        COMMAND_CONTEXT_HEADER in content
        or COMMAND_CONTEXT_SUPERSEDED_HEADER in content
    ):
        return content
    if _USER_MESSAGE_OPEN not in content or _USER_MESSAGE_CLOSE not in content:
        return content
    body = content.split(_USER_MESSAGE_OPEN, 1)[1]
    return body.rsplit(_USER_MESSAGE_CLOSE, 1)[0]


def _active_projection(context: str, user_content: str) -> str:
    return (
        f"<system-reminder>\n{context}\n</system-reminder>\n\n"
        f"{_USER_MESSAGE_OPEN}{user_content}{_USER_MESSAGE_CLOSE}"
    )


def _superseded_projection(command: str, user_content: str) -> str:
    return (
        "<system-reminder>\n"
        f'{COMMAND_CONTEXT_SUPERSEDED_HEADER} command="{command}">\n'
        f"This earlier /{command} activation was superseded by a later "
        f"/{command} activation. Keep the user request as conversation history, "
        "but do not treat this command context as current.\n"
        "</command-context-superseded>\n"
        "</system-reminder>\n\n"
        f"{_USER_MESSAGE_OPEN}{user_content}{_USER_MESSAGE_CLOSE}"
    )


def _stamp_projected_tokens(
    msg: HumanMessage,
    content: str,
    *,
    count_tokens: Any,
) -> dict:
    """Count the transient model-facing form without changing checkpoint data."""
    existing = message_tokens(msg) or {}
    model = str(existing.get("model") or "")
    projected = build_message_tokens(content, model=model, form="raw")
    try:
        counted = count_tokens([msg]) if count_tokens else None
        if isinstance(counted, int):
            projected["raw"] = counted
    except Exception:
        pass
    projected["form"] = "raw"
    return projected


@dataclass
class CommandContextEdit:
    contexts: dict[str, str]
    activated_this_turn: set[str]

    def apply(self, messages: list, *, count_tokens: Any) -> None:
        try:
            active = sorted(
                name
                for name, content in (self.contexts or {}).items()
                if isinstance(content, str) and content
            )
            if not active:
                return

            activations: dict[str, list[int]] = {name: [] for name in active}
            for idx, msg in enumerate(messages):
                if not isinstance(msg, HumanMessage):
                    continue
                name = _activation_name(msg)
                if name is None:
                    text = _content(msg).lstrip()
                    if text.startswith("/"):
                        token = text.split(None, 1)[0].removeprefix("/")
                        name = token if token in self.contexts else None
                if name in activations:
                    activations[name].append(idx)

            for name in active:
                context = self.contexts[name]
                anchors = activations[name]
                # An implicit platform activation (the browser-first side-panel
                # mode) has no slash token in the user's text. Anchor its
                # backend-resolved instruction to the current HumanMessage.
                if not anchors and name in self.activated_this_turn:
                    anchor = next(
                        (
                            idx
                            for idx in range(len(messages) - 1, -1, -1)
                            if isinstance(messages[idx], HumanMessage)
                        ),
                        None,
                    )
                    if anchor is not None:
                        anchors.append(anchor)
                if not anchors:
                    continue
                latest = anchors[-1]
                for anchor in anchors:
                    if anchor < 0 or anchor >= len(messages):
                        continue
                    message = messages[anchor]
                    original = _source_content(message, name)
                    projection = "active" if anchor == latest else "superseded"
                    content = (
                        _active_projection(context, original)
                        if projection == "active"
                        else _superseded_projection(name, original)
                    )
                    message.content = content
                    ak = dict(getattr(message, "additional_kwargs", None) or {})
                    command_context = dict(ak.get("command_context") or {})
                    command_context[name] = {
                        "injected": projection == "active",
                        "projection": projection,
                    }
                    ak["command_context"] = command_context
                    ak["tokens"] = _stamp_projected_tokens(
                        message,
                        content,
                        count_tokens=count_tokens,
                    )
                    message.additional_kwargs = ak
        except Exception:
            pass

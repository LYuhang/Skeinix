"""Middleware: strip the right-click "Attached Context" prefix from older
user messages so multi-turn chats don't balloon the LLM's input.

When a user right-clicks a node on the canvas and picks "As Context",
the next message they send carries a structured prefix prepended to
their typed text by ``demo/handlers/agent_prefix.py::build_context_prefix``::

    [Attached Context — N items]

    ## 1. <label>
      - focus: <focus>
      - workflow: <workflow ref>
      - execution_context: <inline JSON>

    ## 2. ...

    ---

    [User]
    <user typed text>

These prefixes are useful for the turn they were attached to but rarely
needed verbatim afterwards, and they accumulate across history. This
edit keeps the ``keep`` most recent prefixed messages whole; older ones
have everything before the trailing ``[User]\\n`` marker collapsed to a
short note so the model still knows context was once attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage


_CONTEXT_HEADER = "[Attached Context"
_USER_BOUNDARY = "\n[User]\n"
_STRIP_PLACEHOLDER = (
    "[earlier context attachments stripped to save tokens — "
    "ask the user if you need them again]\n"
)


@dataclass(slots=True)
class ContextPrefixStripEdit:
    """Strip the context-prefix block from older HumanMessages.

    Args:
        keep: Number of most-recent context-prefixed messages to leave
            untouched. Default 1 — only the latest attached context is
            visible to the model in full.
    """

    keep: int = 1

    def apply(self, messages: list, *, count_tokens: Any = None) -> None:
        prefixed_indices: list[int] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, HumanMessage):
                continue
            if not _is_context_prefixed(msg.content):
                continue
            prefixed_indices.append(idx)

        if len(prefixed_indices) <= self.keep:
            return

        for idx in prefixed_indices[: -self.keep] if self.keep > 0 else prefixed_indices:
            old = messages[idx]
            new_content = _strip_prefix(old.content)
            messages[idx] = old.model_copy(update={
                "content": new_content,
                "response_metadata": {
                    **getattr(old, "response_metadata", {}),
                    "context_editing": {
                        "cleared": True,
                        "strategy": "context_prefix_strip",
                    },
                },
            })


def _is_context_prefixed(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    if not content.startswith(_CONTEXT_HEADER):
        return False
    return _USER_BOUNDARY in content


def _strip_prefix(content: str) -> str:
    """Drop the ``[Attached Context — …]`` block, keep only the user text
    that followed ``[User]\\n``. A short placeholder note replaces the
    block so the model still sees that context was once attached.
    """
    boundary = content.rfind(_USER_BOUNDARY)
    if boundary == -1:
        return content  # malformed — leave alone defensively
    user_text = content[boundary + len(_USER_BOUNDARY):]
    return _STRIP_PLACEHOLDER + user_text

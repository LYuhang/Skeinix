"""HardContextEdit — dynamic hard-context cleanup + active todo guidance.

This edit operates on ContextEditingMiddleware's model-facing copy only. It never
mutates the checkpointer. Legacy ``<hard-context>`` reminders are stripped, and
when unfinished todo items exist, a fresh ``<todo-reminder>`` HumanMessage is
inserted immediately before the latest AIMessage.

That placement is intentional: for a sequence such as ``AI -> tool -> tool`` the
next model call sees the todo list as guidance before the latest assistant step,
without interrupting tool adjacency or appending a reminder after the model's
own reasoning/output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

HARD_CONTEXT_HEADER = "<hard-context>"
TODO_REMINDER_HEADER = "<todo-reminder>"


def _is_hard_context(msg: Any) -> bool:
    return isinstance(getattr(msg, "content", None), str) and HARD_CONTEXT_HEADER in msg.content


def _is_todo_reminder(msg: Any) -> bool:
    return isinstance(getattr(msg, "content", None), str) and TODO_REMINDER_HEADER in msg.content


def _active_todos(context: Any) -> list[dict]:
    items = getattr(context, "todo_items", None)
    return [
        item for item in (items or [])
        if isinstance(item, dict) and item.get("status") != "done"
    ]


def _todo_message(items: list[dict]) -> HumanMessage:
    lines = [
        "<system-reminder>",
        "<todo-reminder>",
        "Unfinished todo items remain. Use them as guidance for the next assistant step.",
        "After completing an item, immediately update it with the todo tool so the user can see progress.",
        "",
        "Unfinished todo items:",
    ]
    for item in items[:12]:
        lines.append(
            f"- [{item.get('status', 'pending')}] {item.get('id', '?')}: {item.get('text', '')}"
        )
    lines.extend(["</todo-reminder>", "</system-reminder>"])
    return HumanMessage(content="\n".join(lines))


def _insert_before_latest_ai(messages: list, reminder: HumanMessage) -> None:
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], AIMessage):
            messages.insert(idx, reminder)
            return
    messages.append(reminder)


@dataclass
class HardContextEdit:
    context: Any

    def apply(self, messages: list, *, count_tokens: Any) -> None:
        try:
            holder = self.context if isinstance(self.context, dict) else None
            context = holder.get("context") if holder is not None else self.context
            for i in range(len(messages) - 1, -1, -1):
                if _is_hard_context(messages[i]) or _is_todo_reminder(messages[i]):
                    del messages[i]
            todos = _active_todos(context)
            if todos:
                _insert_before_latest_ai(messages, _todo_message(todos))
        except Exception:
            pass

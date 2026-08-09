"""Build the durable runtime-neutral Chat transcript from product events."""

from __future__ import annotations

from typing import Any

from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope


class ChatTranscriptWriter:
    """Persist completed visible messages independently of an Agent SDK.

    Streaming fragments remain in ``agent_run_events``. This writer keeps only
    the current in-flight assembly and commits a canonical ``chat_messages`` row
    at a message boundary. A hard failure flushes an interrupted assistant
    message so product history does not depend on a Runtime checkpoint.
    """

    def __init__(self, *, tenant_id: str, user_id: str, chat_id: str, turn_id: str):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.chat_id = chat_id
        self.turn_id = turn_id
        self._messages: dict[str, dict[str, Any]] = {}

    async def _persist(self, message_id: str, state: dict, *, status: str) -> None:
        role = str(state.get("role") or "assistant")
        content = {
            "schema_version": 2,
            "message_type": "text",
            "visibility": "visible",
            "text": str(state.get("text") or ""),
            "attachments": list(state.get("attachments") or []),
            "tool_calls": list(state.get("tool_calls") or []),
        }
        async with session_scope(tenant_id=self.tenant_id) as session:
            repo = ChatRepo(session, self.user_id)
            await repo.persist_message(
                self.chat_id,
                {
                    "message_id": message_id,
                    "turn_id": self.turn_id,
                    "role": role,
                    "content": content,
                    "meta": {"status": status},
                },
            )
            await repo.commit()

    async def consume(self, event_type: str, payload: dict) -> None:
        if event_type != "CHAT_EVENT":
            return
        kind = str(payload.get("type") or "")
        if kind == "todo_update":
            items = payload.get("items")
            normalized = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
            async with session_scope(tenant_id=self.tenant_id) as session:
                repo = ChatRepo(session, self.user_id)
                await repo.set_todo_items(self.chat_id, normalized)
                await repo.commit()
            return
        message_id = str(payload.get("message_id") or "")
        if kind == "message_start" and message_id:
            self._messages[message_id] = {
                "role": payload.get("role") or "assistant",
                "text": payload.get("content") or "",
                "attachments": payload.get("attachments") or [],
                "tool_calls": [],
            }
            return
        if kind in {"message_replace", "message_delta"} and message_id:
            state = self._messages.setdefault(
                message_id,
                {"role": "assistant", "text": "", "attachments": [], "tool_calls": []},
            )
            value = str(payload.get("content") or payload.get("delta") or "")
            if kind == "message_replace":
                state["text"] = value
            else:
                state["text"] = str(state.get("text") or "") + value
            return
        if kind == "tool_start":
            carrier_id = message_id
            if not carrier_id:
                carrier_id = f"{self.chat_id}:{self.turn_id}:tools"
            state = self._messages.setdefault(
                carrier_id,
                {"role": "assistant", "text": "", "attachments": [], "tool_calls": []},
            )
            state["tool_calls"].append({
                "id": str(payload.get("tool_call_id") or ""),
                "type": "function",
                "function": {
                    "name": str(payload.get("name") or ""),
                    "arguments": payload.get("arguments") or "{}",
                },
                "invocation": (
                    payload.get("invocation")
                    if isinstance(payload.get("invocation"), dict)
                    else None
                ),
            })
            return
        if kind == "message_end" and message_id:
            state = self._messages.pop(message_id, None)
            if state is not None:
                await self._persist(message_id, state, status="completed")
            return
        if kind == "tool_end":
            tool_call_id = str(payload.get("tool_call_id") or "")
            if not tool_call_id:
                return
            # The assistant tool-call carrier must precede every Tool result in
            # the canonical transcript. Tool results are committed immediately,
            # while the carrier used to remain buffered until ``close()``;
            # history consequently returned ``tool`` before ``assistant`` and
            # the frontend correctly discarded the orphan result, leaving a
            # permanently-running tool after refresh.
            #
            # All sibling tool_start events are emitted before the first
            # tool_end, so persisting the shared carrier here also preserves
            # parallel tool-call announcements without duplicating the row.
            carrier = next(
                (
                    (carrier_id, state)
                    for carrier_id, state in self._messages.items()
                    if any(
                        isinstance(call, dict)
                        and str(call.get("id") or "") == tool_call_id
                        for call in state.get("tool_calls") or []
                    )
                ),
                None,
            )
            if carrier is not None:
                carrier_id, state = carrier
                self._messages.pop(carrier_id, None)
                await self._persist(carrier_id, state, status="completed")
            async with session_scope(tenant_id=self.tenant_id) as session:
                repo = ChatRepo(session, self.user_id)
                await repo.persist_message(
                    self.chat_id,
                    {
                        "message_id": f"{self.chat_id}:{self.turn_id}:tool:{tool_call_id}",
                        "turn_id": self.turn_id,
                        "role": "tool",
                        "content": {
                            "schema_version": 2,
                            "message_type": "tool_result",
                            "visibility": "visible",
                            "text": str(payload.get("content") or ""),
                            "attachments": [],
                            "tool_calls": [],
                            "tool_call_id": tool_call_id,
                            "artifact": (
                                payload.get("artifact")
                                if isinstance(payload.get("artifact"), dict)
                                else None
                            ),
                            "invocation": (
                                payload.get("invocation")
                                if isinstance(payload.get("invocation"), dict)
                                else None
                            ),
                        },
                        "meta": {
                            "status": str(payload.get("status") or "done"),
                            "tool_name": str(payload.get("name") or ""),
                        },
                    },
                )
                await repo.commit()

    async def close(self) -> None:
        pending = list(self._messages.items())
        self._messages.clear()
        for message_id, state in pending:
            # Empty protocol carriers do not belong in visible history.
            if state.get("text") or state.get("tool_calls"):
                await self._persist(message_id, state, status="interrupted")

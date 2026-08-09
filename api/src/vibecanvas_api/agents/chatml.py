"""Bidirectional ChatML <-> LangChain conversion (single home).

to_chatml* preserves the domain transforms the ad-hoc agent.py::_langchain_to_chatml
applied (strip_context_prefix on user turns, attachment lifting) — convert_to_openai_messages
does NOT do these. from_chatml uses the langchain_core converter.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.messages import (
    AIMessage, HumanMessage, SystemMessage, ToolMessage, convert_to_messages,
)

from vibecanvas_api.agents.prefix import strip_context_prefix


def _extract_meta(msg: Any) -> dict | None:
    """The ``_meta`` annotation rides on ``additional_kwargs`` (Human) /
    ``response_metadata`` (AI/Tool), so both locations are supported."""
    ak = getattr(msg, "additional_kwargs", None) or {}
    if "_meta" in ak:
        return ak["_meta"]
    rm = getattr(msg, "response_metadata", None) or {}
    return rm.get("_meta")


def to_chatml_message(msg: Any) -> dict:
    meta = _extract_meta(msg)
    if isinstance(msg, HumanMessage):
        out: Dict[str, Any] = {"role": "user", "content": strip_context_prefix(msg.content or "")}
        atts = (msg.additional_kwargs or {}).get("attachments")
        if isinstance(atts, list) and atts:
            out["attachments"] = atts
        if meta is not None:
            out["_meta"] = meta
        return out
    if isinstance(msg, AIMessage):
        d: Dict[str, Any] = {"role": "assistant", "content": msg.text or ""}
        if msg.tool_calls:
            d["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"],
                              "arguments": (json.dumps(tc["args"], ensure_ascii=False)
                                            if isinstance(tc["args"], dict) else tc["args"])}}
                for tc in msg.tool_calls
            ]
        if meta is not None:
            d["_meta"] = meta
        return d
    if isinstance(msg, ToolMessage):
        name = msg.name or ""
        artifact = getattr(msg, "artifact", None)
        if not name and isinstance(artifact, dict):
            meta = artifact.get("meta")
            if isinstance(meta, dict) and isinstance(meta.get("tool"), str):
                name = meta["tool"]
        d = {"role": "tool", "content": msg.content or "",
             "tool_call_id": msg.tool_call_id or "", "name": name}
        if isinstance(artifact, dict):
            d["artifact"] = artifact
        if meta is not None:
            d["_meta"] = meta
        return d
    text = getattr(msg, "text", None) or (str(msg.content) if getattr(msg, "content", None) else "")
    return {"role": "unknown", "content": text}


def to_chatml(messages: list) -> list[dict]:
    return [to_chatml_message(m) for m in messages if not isinstance(m, SystemMessage)]


def from_chatml(dicts: list[dict]) -> list:
    """Inverse of ``to_chatml``. The non-standard ``_meta`` key has no home in the
    langchain converter (it would be dropped), so strip it before converting and
    re-attach it onto ``additional_kwargs`` afterward."""
    metas = [d.get("_meta") for d in dicts]
    cleaned = [{k: v for k, v in d.items() if k != "_meta"} for d in dicts]
    messages = convert_to_messages(cleaned)
    for msg, meta in zip(messages, metas):
        if meta is not None:
            msg.additional_kwargs["_meta"] = meta
    return messages

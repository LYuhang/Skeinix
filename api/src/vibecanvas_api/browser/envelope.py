"""Shared envelope for browser ping, command, and observation messages."""
from __future__ import annotations
import json

_REQUIRED = ("kind", "id", "channel", "transport")

def encode(kind: str, *, id: str, channel: str, transport: str,
           data: dict | None = None, producer: str | None = None) -> str:
    return json.dumps({"v": 1, "kind": kind, "id": id, "channel": channel,
                       "transport": transport, "data": data, "producer": producer},
                      separators=(",", ":"))

def decode(raw: str) -> dict:
    try:
        d = json.loads(raw)
    except Exception as e:
        raise ValueError(f"malformed envelope: {e}") from e
    if not isinstance(d, dict) or any(k not in d for k in _REQUIRED):
        raise ValueError("envelope missing required fields")
    return d

"""Server-sent-event formatter shared by streaming routes.

Wire format (W3C SSE spec):
    id: <optional-event-id>
    event: <event-name>
    data: <single-line json>
    \n
"""

from __future__ import annotations

import json
import math
from typing import Any


def _strict_json_safe(value: Any) -> Any:
    """Convert payloads into browser-parseable JSON values.

    Python's ``json.dumps`` defaults to ``allow_nan=True`` and emits bare
    ``NaN``/``Infinity`` tokens. Those are not valid JSON for ``JSON.parse`` in
    browsers, so one bad workflow input/result can terminate the SSE consumer
    after the first running frame. Preserve the information as strings.
    """
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, dict):
        return {str(k): _strict_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_strict_json_safe(v) for v in value]
    return value


def format_event(event_name: str, payload: Any, event_id: int | str | None = None) -> bytes:
    """Serialize an SSE event with the SIGNAL_TYPE in the event field."""
    if not event_name or not isinstance(event_name, str):
        raise ValueError(f"event_name must be non-empty str, got {event_name!r}")
    # ``default=str`` (M3): an EXEC_UPDATE terminal frame's ``outputs`` (the
    # engine's raw ``__end__`` scope) can carry non-JSON-native values
    # (numpy / Decimal / bytes / set / PIL) that no upstream mapper
    # stringified. A bare ``json.dumps`` would raise HERE, mid-stream, and
    # tear down the whole SSE response. Degrading to ``str()`` mirrors
    # ``sandbox_entry.py``'s event-line serialization so the wire never
    # crashes on an exotic value.
    body = json.dumps(
        _strict_json_safe(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event_name}\ndata: {body}\n\n".encode("utf-8")

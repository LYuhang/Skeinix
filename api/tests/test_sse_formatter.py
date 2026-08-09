"""SSE format_event produces correct wire bytes."""

from __future__ import annotations

import json
import math

import pytest

from vibecanvas_api.streaming.sse import format_event


def test_basic_event():
    out = format_event("CHAT_UPDATE", {"content": "hi"})
    assert out == b'event: CHAT_UPDATE\ndata: {"content":"hi"}\n\n'


def test_unicode_payload_preserved():
    out = format_event("VIBE_ACTION", {"name": "你好"})
    assert "你好".encode() in out
    assert out.endswith(b"\n\n")


def test_event_id_is_optional_and_serialized_when_present():
    out = format_event("CHAT_EVENT", {"type": "done"}, event_id=42)
    assert out.startswith(b'id: 42\nevent: CHAT_EVENT\n')
    assert out.endswith(b'\n\n')


def test_empty_event_name_raises():
    with pytest.raises(ValueError):
        format_event("", {"x": 1})


def test_payload_is_single_line():
    out = format_event("X", {"nested": {"k": "v", "list": [1, 2]}})
    decoded = out.decode("utf-8")
    assert decoded.count("\n") == 3  # event:\n + data:...\n + \n


def test_non_finite_floats_are_browser_parseable_json():
    out = format_event(
        "EXEC_UPDATE",
        {
            "node_id": "node_1",
            "inputs": {"x": math.nan, "y": math.inf, "z": -math.inf},
        },
    )
    data_line = out.decode("utf-8").splitlines()[1]
    assert data_line.startswith("data: ")
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["inputs"] == {
        "x": "NaN",
        "y": "Infinity",
        "z": "-Infinity",
    }

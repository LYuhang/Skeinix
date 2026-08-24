from __future__ import annotations

from vibecanvas_api.services.agent_runtime.history_recovery import (
    build_durable_history_snapshot,
)


def _row(index: int, *, role: str = "assistant", text: str = "message") -> dict:
    return {
        "message_id": f"message-{index}",
        "turn_id": f"turn-{index}",
        "role": role,
        "content": {
            "visibility": "visible",
            "text": text,
            "attachments": [],
            "tool_calls": [],
        },
        "meta": {"status": "completed"},
    }


def test_history_projection_preserves_roles_tools_and_watermark():
    rows = [
        _row(1, role="user", text="Create the report"),
        {
            **_row(2),
            "content": {
                "visibility": "visible",
                "text": "",
                "attachments": [{
                    "name": "report.pdf",
                    "path": "/data/report.pdf",
                    "type": "application/pdf",
                }],
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"/data/report.pdf"}',
                    },
                }],
            },
        },
        {
            **_row(3, role="tool", text="done"),
            "content": {
                "visibility": "visible",
                "text": "done",
                "attachments": [],
                "tool_calls": [],
                "tool_call_id": "call-1",
            },
        },
        {
            **_row(4, role="user", text="hidden control"),
            "content": {"visibility": "hidden", "text": "hidden control"},
        },
    ]

    snapshot = build_durable_history_snapshot(rows)

    assert [item.role for item in snapshot.messages] == [
        "user",
        "assistant",
        "tool",
    ]
    assert snapshot.messages[1].tool_calls[0].name == "read_file"
    assert snapshot.messages[1].attachments[0].path == "/data/report.pdf"
    assert snapshot.messages[2].tool_call_id == "call-1"
    assert snapshot.last_turn_id == "turn-4"


def test_history_projection_reports_rows_omitted_by_tail_query():
    snapshot = build_durable_history_snapshot(
        [_row(9, role="user", text="recent")],
        source_total=600,
    )

    assert snapshot.truncated is True
    assert snapshot.omitted_message_count == 599

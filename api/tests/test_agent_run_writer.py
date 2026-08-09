from __future__ import annotations

from vibecanvas_api.services.agent_run_writer import AgentRunWriter


class RecordingWriter(AgentRunWriter):
    def __init__(self) -> None:
        super().__init__(run_id="t_test", tenant_id="00000000-0000-0000-0000-000000000001")
        self.rows: list[tuple[int, str, dict]] = []

    async def _append(self, seq: int, event_type: str, payload: dict) -> None:
        self.rows.append((seq, event_type, payload))


async def test_writer_persists_cumulative_text_and_tool_events_in_order():
    writer = RecordingWriter()
    await writer.emit(1, "started", {"turn_id": "t_test"})
    await writer.emit(2, "CHAT_EVENT", {
        "type": "message_replace", "message_id": "m1", "content": "a",
    })
    await writer.emit(3, "CHAT_EVENT", {
        "type": "message_replace", "message_id": "m1", "content": "ab",
    })
    await writer.emit(4, "CHAT_EVENT", {
        "type": "tool_start", "message_id": "tm1", "tool_call_id": "tc1",
    })

    assert [row[0] for row in writer.rows] == [1, 2, 3, 4]
    assert writer.rows[2][2]["content"] == "ab"


async def test_writer_persists_latest_replace_before_close():
    writer = RecordingWriter()
    await writer.emit(1, "CHAT_EVENT", {
        "type": "message_replace", "message_id": "m1", "content": "a",
    })
    await writer.emit(2, "CHAT_EVENT", {
        "type": "message_replace", "message_id": "m1", "content": "latest",
    })
    await writer.close()

    assert writer.rows[-1][0] == 2
    assert writer.rows[-1][2]["content"] == "latest"

import asyncio
import json
import pytest
from vibecanvas_api.browser.host import CommandHost, CommandResultUnknown, TransportClosed
from vibecanvas_api.browser.registry import TransportSendFailed
from vibecanvas_api.browser.commands import Cmd


async def test_send_command_awaits_matching_observation(monkeypatch):
    host = CommandHost(write_media=lambda obs, **k: obs)  # identity media-resolver for this test
    sent = {}
    from vibecanvas_api.browser import host as host_mod

    async def fake_send_to(tid, raw):
        sent["raw"] = raw
        return True
    monkeypatch.setattr(host_mod.registry, "send_to", fake_send_to)

    async def respond():
        await asyncio.sleep(0.01)
        cmd = json.loads(sent["raw"])
        host.resolve_observation(json.dumps({
            "v": 1, "kind": "observation", "id": cmd["id"], "channel": cmd["channel"],
            "transport": cmd["transport"], "producer": None,
            "data": {"ok": True, "target_id": "T0", "text": "done"}}))

    asyncio.create_task(respond())
    obs = await host.send_command(transport_id="t:b", channel="chat:1", cmd=Cmd.READ_TEXT,
                                  args={"selector": "h1"}, target_id="T0", producer="agent")
    assert obs.ok and obs.data["text"] == "done"
    command = json.loads(sent["raw"])
    assert command["data"]["cmd"] == "read_text"
    assert command["data"]["args"]["command_id"] == command["id"]


async def test_send_command_raises_when_transport_closed(monkeypatch):
    host = CommandHost(write_media=lambda obs, **k: obs)
    from vibecanvas_api.browser import host as host_mod

    async def fake_send_to(tid, raw):
        return False
    monkeypatch.setattr(host_mod.registry, "send_to", fake_send_to)
    with pytest.raises(TransportClosed):
        await host.send_command(transport_id="t:gone", channel="chat:1", cmd=Cmd.NAVIGATE,
                                args={"url": "x"}, target_id="T0", producer="agent")


async def test_send_command_times_out(monkeypatch):
    host = CommandHost(write_media=lambda obs, **k: obs)
    from vibecanvas_api.browser import host as host_mod

    async def fake_send_to(tid, raw):
        return True
    monkeypatch.setattr(host_mod.registry, "send_to", fake_send_to)
    with pytest.raises(asyncio.TimeoutError):
        await host.send_command(transport_id="t:b", channel="c", cmd=Cmd.SNAPSHOT,
                                args={}, target_id="T0", producer="agent", timeout_s=0.05)


async def test_send_command_reports_uncertain_delivery_when_socket_write_fails(monkeypatch):
    host = CommandHost(write_media=lambda obs, **k: obs)
    from vibecanvas_api.browser import host as host_mod

    async def fake_send_to(tid, raw):
        raise TransportSendFailed("write failed")

    monkeypatch.setattr(host_mod.registry, "send_to", fake_send_to)
    with pytest.raises(CommandResultUnknown):
        await host.send_command(
            transport_id="t:b",
            channel="chat:1",
            cmd=Cmd.CLICK,
            args={"handle": "h1"},
            target_id="T0",
            producer="agent",
        )

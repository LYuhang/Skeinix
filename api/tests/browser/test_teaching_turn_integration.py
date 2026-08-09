"""Browser-teaching integration at the backend boundary:

    fake extension <--> transport registry <--> command host <--> AgentBrowser

A fake extension registered on the registry echoes an observation for every
command the host ships; AgentBrowser awaits and returns it as a plain dict. The
media path proves the host's per-turn media writer substitutes bytes -> VFS path
(stubbed here to avoid a DB), so the tool output stays a string.
"""
import asyncio
import json

import pytest

from vibecanvas_api.browser.registry import registry
from vibecanvas_api.browser.host import command_host
from vibecanvas_api.browser.commands import Observation
from vibecanvas_api.browser.agent_channel import AgentBrowser


def _make_fake_extension(reply_data):
    """Return a registry send-fn that, on each inbound command, schedules an
    observation reply back through command_host.resolve_observation (mirrors the
    WS hub). ``reply_data(cmd, args) -> dict`` builds the observation data."""
    async def _send(raw: str):
        msg = json.loads(raw)
        cid = msg["id"]
        d = msg["data"]
        data = reply_data(d["cmd"], d.get("args") or {})

        async def _respond():
            obs_raw = json.dumps({
                "v": 1, "kind": "observation", "id": cid,
                "channel": msg["channel"], "transport": msg["transport"],
                "producer": None, "data": data,
            })
            command_host.resolve_observation(obs_raw)

        asyncio.create_task(_respond())
    return _send


@pytest.mark.asyncio
async def test_navigate_round_trips_through_registry():
    def reply(cmd, args):
        return {"ok": True, "target_id": "T0",
                "final_url": args["url"], "title": "OK"}

    registry.register("t1:b1", _make_fake_extension(reply))
    try:
        ab = AgentBrowser(command_host, transport_id="t1:b1", channel="chat:c1",
                          workspace_scope_id="chat_ws_1", tenant_id="t1")
        obs = await ab.navigate(url="https://x.test")
        assert obs["ok"] is True
        assert obs["data"]["final_url"] == "https://x.test"
        assert obs["data"]["title"] == "OK"
    finally:
        registry.unregister("t1:b1")


@pytest.mark.asyncio
async def test_screenshot_media_substituted_to_path(monkeypatch):
    """Media bytes in the observation become a VFS path before AgentBrowser returns
    (the host applies the per-turn media writer). Stub the writer to avoid a DB."""
    import vibecanvas_api.browser.agent_channel as ac

    def fake_host_media_writer(workspace_scope_id, tenant_id):
        def _writer(obs, *, transport_id=None):
            return Observation(
                id=obs.id, ok=obs.ok,
                data={"media": [{"slot": "screenshot",
                                 "path": "/data/browser-media/deadbeef.png"}]},
                media=[{"slot": "screenshot",
                        "path": "/data/browser-media/deadbeef.png"}],
                target_id=obs.target_id, error=obs.error)
        return _writer

    monkeypatch.setattr(ac, "host_media_writer", fake_host_media_writer)

    def reply(cmd, args):
        # The extension returns media bytes (b64); the command-scoped writer turns
        # them into a path. We emit a media slot so command_host applies it.
        return {"ok": True, "target_id": "T0",
                "media": [{"slot": "screenshot", "b64": "iVBORw0KGgo=", "ext": "png",
                           "mime": "image/png"}]}

    registry.register("t1:b1", _make_fake_extension(reply))
    try:
        ab = AgentBrowser(command_host, transport_id="t1:b1", channel="chat:c1",
                          workspace_scope_id="chat_ws_1", tenant_id="t1")
        obs = await ab.screenshot(full_page=False)
        m = obs["media"][0]
        assert "b64" not in m and m["slot"] == "screenshot"
        assert m["path"] == "/data/browser-media/deadbeef.png"
    finally:
        registry.unregister("t1:b1")

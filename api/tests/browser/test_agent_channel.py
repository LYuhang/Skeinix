"""AgentBrowser facade over ``command_host.send_command``.

AgentBrowser binds to the shared `Cmd`, `Observation`, command host, and media
writer contracts. It holds a
(transport_id, channel, producer) binding, sets command_host._write_media to a
teaching-scope writer, maps Python method name -> Cmd, and returns the decoded
Observation as a plain dict (media already substituted to VFS paths by the host).
"""
import pytest

from vibecanvas_api.browser.agent_channel import AgentBrowser
from vibecanvas_api.browser.commands import Cmd, Observation


class FakeHost:
    """Stands in for the command-host singleton."""
    def __init__(self, reply=None):
        self.sent = []
        self._write_media = None
        self._reply = reply

    async def send_command(self, *, transport_id, channel, cmd, args,
                           target_id, producer, timeout_s=30.0, write_media=None):
        self.sent.append({
            "transport_id": transport_id, "channel": channel, "cmd": cmd,
            "args": args, "target_id": target_id, "producer": producer,
            "timeout_s": timeout_s,
            "write_media": write_media,
        })
        if self._reply is not None:
            return self._reply(cmd, args)
        return Observation(id="x", ok=True, data={}, media=[],
                           target_id=None, error=None)


@pytest.mark.asyncio
async def test_navigate_maps_to_cmd_and_passes_args():
    def reply(cmd, args):
        return Observation(id="x", ok=True,
                           data={"final_url": args["url"], "title": "T"},
                           media=[], target_id=None, error=None)
    host = FakeHost(reply=reply)
    ab = AgentBrowser(host, transport_id="t1:b1", channel="chat:c1",
                      workspace_scope_id="chat_ws_1", tenant_id="t1")
    obs = await ab.navigate(url="https://x.test", wait_until="load")
    assert obs["ok"] and obs["data"]["final_url"] == "https://x.test"
    sent = host.sent[0]
    assert sent["cmd"] is Cmd.NAVIGATE
    assert sent["args"] == {"url": "https://x.test", "wait_until": "load"}
    assert sent["transport_id"] == "t1:b1" and sent["channel"] == "chat:c1"
    assert sent["producer"] == "agent"


@pytest.mark.asyncio
async def test_none_args_are_dropped():
    host = FakeHost()
    ab = AgentBrowser(host, transport_id="t1:b1", channel="chat:c1",
                      workspace_scope_id="chat_ws_1", tenant_id="t1")
    await ab.snapshot(scope=None, prune=True)
    assert host.sent[0]["args"] == {"prune": True}  # None scope dropped


@pytest.mark.asyncio
async def test_screenshot_sets_media_writer_for_scope(monkeypatch):
    """The host's _write_media is bound to the chat workspace scope for the call, so a
    media observation comes back with VFS PATHS (host did write_observation_media).
    We assert AgentBrowser installs the workspace writer, not that it re-implements it."""
    captured = {}

    def fake_host_media_writer(workspace_scope_id, tenant_id):
        captured["workspace_scope_id"] = workspace_scope_id
        captured["tenant_id"] = tenant_id

        def _writer(obs, *, transport_id=None, cmd=None, args=None):
            # Simulate the host having substituted bytes -> path.
            return Observation(id=obs.id, ok=obs.ok,
                               data={"media": [{"slot": "screenshot",
                                                "path": "/data/browser-media/ab.png"}]},
                               media=[{"slot": "screenshot",
                                       "path": "/data/browser-media/ab.png"}],
                               target_id=None, error=None)
        return _writer

    import vibecanvas_api.browser.agent_channel as ac
    monkeypatch.setattr(ac, "host_media_writer", fake_host_media_writer)

    def reply(cmd, args):
        # The host applies the command-scoped writer when media is present.
        raw = Observation(id="x", ok=True, data={},
                          media=[{"slot": "screenshot", "b64": "..", "ext": "png"}],
                          target_id=None, error=None)
        writer = host.sent[-1]["write_media"]
        return writer(raw, transport_id="t1:b1")

    host = FakeHost(reply=reply)
    ab = AgentBrowser(host, transport_id="t1:b1", channel="chat:c1",
                      workspace_scope_id="chat_ws_1", tenant_id="t1")
    obs = await ab.screenshot(full_page=False)
    m = obs["media"][0]
    assert "b64" not in m and m["slot"] == "screenshot"
    assert m["path"] == "/data/browser-media/ab.png"
    assert captured["workspace_scope_id"] == "chat_ws_1" and captured["tenant_id"] == "t1"
    assert host._write_media is None


@pytest.mark.asyncio
async def test_act_forwards_purpose_and_expect():
    host = FakeHost()
    ab = AgentBrowser(host, transport_id="t1:b1", channel="chat:c1",
                      workspace_scope_id="chat_ws_1", tenant_id="t1")
    await ab.submit(handle="h1", purpose="finish order",
                    expect="success toast")
    sent = host.sent[0]
    assert sent["cmd"] is Cmd.SUBMIT
    assert sent["args"]["purpose"] == "finish order"
    assert sent["args"]["expect"] == "success toast"


@pytest.mark.asyncio
async def test_error_observation_surfaces():
    def reply(cmd, args):
        return Observation(id="x", ok=False, data={}, media=[],
                           target_id=None, error="boom")
    host = FakeHost(reply=reply)
    ab = AgentBrowser(host, transport_id="t1:b1", channel="chat:c1",
                      workspace_scope_id="chat_ws_1", tenant_id="t1")
    obs = await ab.click(handle="h1")
    assert obs["ok"] is False and obs["error"] == "boom"


def test_unknown_command_method_absent():
    ab = AgentBrowser(FakeHost(), transport_id="t1:b1", channel="chat:c1",
                      workspace_scope_id="chat_ws_1", tenant_id="t1")
    assert not hasattr(ab, "definitely_not_a_command")

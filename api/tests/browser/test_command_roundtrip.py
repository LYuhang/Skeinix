"""End-to-end over the real WS hub: a fake plugin connects, the host send_command
ships a frame, the fake plugin echoes an observation, send_command resolves it.
Uses Starlette TestClient (httpx ASGITransport can't do WS)."""

import json
import time
from fastapi import FastAPI
from starlette.testclient import TestClient
from vibecanvas_api.config import config
from vibecanvas_api.browser.scoped_token import mint_scoped_token
from vibecanvas_api.browser.ws_auth import build_browser_ws_protocols
from vibecanvas_api.browser.commands import Cmd
from vibecanvas_api.browser.host import CommandHost
from vibecanvas_api.browser.registry import registry
from vibecanvas_api.routes.browser import router as browser_router


def test_command_roundtrip_over_ws(monkeypatch):
    # This test exercises the real browser transport route and CommandHost, but
    # deliberately does not boot the unrelated application lifespan (database
    # migrations, checkpointer, sandbox reaper, background-agent watcher).  A
    # previous full-suite run could otherwise inherit process-global watcher
    # state and block before the WebSocket handshake even began.  Mounting the
    # production router keeps the protocol integration boundary real while
    # making the test hermetic.
    app = FastAPI()
    app.include_router(browser_router)
    tok = mint_scoped_token(
        "u1",
        "t1",
        "wf1",
        config.browser_token_secret,
        browser_id="b1",
        extension_id=config.browser_extension_id,
        session_id="00000000-0000-0000-0000-000000000001",
        session_generation=1,
        session_audience="extension",
    )
    host = CommandHost(write_media=lambda obs, **k: obs)  # no real VFS in this test
    # point the route's module-level command_host at our test instance (lazy access)
    import vibecanvas_api.browser.host as host_mod
    import vibecanvas_api.routes.browser as browser_routes

    async def session_is_live(_scoped):
        return True

    monkeypatch.setattr(browser_routes, "_browser_session_is_live", session_is_live)
    original = host_mod.command_host
    host_mod.command_host = host
    try:
        with TestClient(app).websocket_connect(
            "/api/v1/browser/ws",
            subprotocols=build_browser_ws_protocols(tok, "b1"),
            headers={"origin": f"chrome-extension://{config.browser_extension_id}"},
        ) as ws:
            # The ASGI accept frame reaches TestClient just before ws_hub adds
            # its sender to the process-local registry. Waiting for the actual
            # transport-ready condition avoids racing send_command into a
            # fail-closed TransportClosed result and then blocking forever on
            # receive_json().
            deadline = time.monotonic() + 2.0
            while not registry.is_connected("t1:u1:b1"):
                assert time.monotonic() < deadline, "browser transport not ready"
                time.sleep(0.001)

            async def driver():
                return await host.send_command(
                    transport_id="t1:u1:b1",
                    channel="chat:1",
                    cmd=Cmd.READ_TEXT,
                    args={"selector": "h1"},
                    target_id="T0",
                    producer="agent",
                    timeout_s=5,
                )

            # Schedule the producer on TestClient's own portal. Calling the
            # WebSocket sender from an unrelated asyncio.run() loop is not a
            # supported Starlette boundary and can deadlock under full-suite
            # process state even when an isolated run happens to pass.
            command_result = ws.portal.start_task_soon(driver)
            cmd = ws.receive_json()  # the host's command frame
            assert cmd["kind"] == "command" and cmd["data"]["cmd"] == "read_text"
            ws.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "kind": "observation",
                        "id": cmd["id"],
                        "channel": cmd["channel"],
                        "transport": cmd["transport"],
                        "producer": None,
                        "data": {"ok": True, "target_id": "T0", "text": "Hello"},
                    }
                )
            )
            obs = command_result.result(timeout=5)
            assert obs.data["text"] == "Hello"
    finally:
        host_mod.command_host = original

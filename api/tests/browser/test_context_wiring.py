from vibecanvas_api import agent as agent_mod
from vibecanvas_api.schemas.chat import MessagePostBody


# Browser mode is driven purely by `/browser` (active_modes) — no feature flag.
# The per-turn channel binds iff "browser" is active AND a live transport exists.


def test_no_channel_when_not_browser():
    assert agent_mod._build_browser_channel(set(), "t1", "u1", "c1") is None


def test_no_channel_when_transport_absent():
    from vibecanvas_api.browser.registry import registry
    registry.unregister("t1:u1:b1")  # ensure not connected
    assert agent_mod._build_browser_channel({"browser"}, "t1", "u1", "c1") is None


def test_channel_built_when_transport_live():
    from vibecanvas_api.browser.registry import registry

    async def _send(_raw): ...
    registry.register("t1:u1:b1", _send)
    try:
        ch = agent_mod._build_browser_channel({"browser"}, "t1", "u1", "c1")
        assert ch is not None
        # Bound to the per-browser transport + the chat channel (§4.3).
        assert ch.transport_id == "t1:u1:b1" and ch.channel == "chat:c1"
    finally:
        registry.unregister("t1:u1:b1")


def test_multiple_live_browser_entities_fail_closed():
    from vibecanvas_api.browser.registry import registry

    async def _send(_raw): ...
    registry.register("t1:u1:b1", _send)
    registry.register("t1:u1:b2", _send)
    try:
        assert agent_mod._build_browser_channel({"browser"}, "t1", "u1", "c1") is None
    finally:
        registry.unregister("t1:u1:b1")
        registry.unregister("t1:u1:b2")


def test_browser_transport_routing_is_user_scoped():
    from vibecanvas_api.browser.registry import registry

    async def _send(_raw): ...
    registry.register("t1:u2:b1", _send)
    try:
        assert agent_mod._build_browser_channel({"browser"}, "t1", "u1", "c1") is None
        assert agent_mod._build_browser_channel({"browser"}, "t1", "u2", "c2") is not None
    finally:
        registry.unregister("t1:u2:b1")


def test_old_websocket_cannot_unregister_replacement():
    from vibecanvas_api.browser.registry import TransportRegistry

    local = TransportRegistry()

    async def old_sender(_raw): ...
    async def new_sender(_raw): ...

    local.register("t1:u1:b1", old_sender)
    local.register("t1:u1:b1", new_sender)
    assert local.unregister("t1:u1:b1", old_sender) is False
    assert local.is_connected("t1:u1:b1") is True
    assert local.unregister("t1:u1:b1", new_sender) is True


def test_agent_context_has_browser_field():
    ctx = agent_mod.AgentContext(browser="sentinel")
    assert ctx.browser == "sentinel"


def test_turn_and_agent_context_do_not_expose_browser_topology():
    forbidden = {
        "browser",
        "browser_id",
        "browser_client_id",
        "browser_window_id",
        "browser_panel_context_id",
        "client_context_id",
    }
    assert forbidden.isdisjoint(MessagePostBody.model_fields)
    # ``browser`` itself is the private transport binding assembled by the
    # backend; only topology coordinates are forbidden in Agent Context.
    assert (forbidden - {"browser"}).isdisjoint(agent_mod.AgentContext.model_fields)

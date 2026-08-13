from __future__ import annotations

import pytest

from vibecanvas_api.browser.playwright_registry import PlaywrightControllerRegistry


@pytest.mark.asyncio
async def test_playwright_controller_registry_is_transport_and_chat_scoped():
    registry = PlaywrightControllerRegistry()
    received: list[dict] = []

    async def send(message: dict) -> None:
        received.append(message)

    registry.register(
        transport_id="tenant:user:browser",
        channel="chat:allowed",
        send=send,
    )
    assert not await registry.forward_extension_message(
        transport_id="tenant:user:browser",
        channel="chat:other",
        message={"id": 1},
    )
    assert not await registry.forward_extension_message(
        transport_id="tenant:other:browser",
        channel="chat:allowed",
        message={"id": 1},
    )
    assert await registry.forward_extension_message(
        transport_id="tenant:user:browser",
        channel="chat:allowed",
        message={"id": 1, "result": {}},
    )
    assert received == [{"id": 1, "result": {}}]


@pytest.mark.asyncio
async def test_stale_controller_cannot_unregister_replacement():
    registry = PlaywrightControllerRegistry()

    async def old(_message: dict) -> None:
        pass

    async def new(_message: dict) -> None:
        pass

    registry.register(transport_id="t", channel="c", send=old)
    registry.register(transport_id="t", channel="c", send=new)
    assert not registry.unregister(
        transport_id="t",
        channel="c",
        sender=old,
    )
    assert registry.unregister(
        transport_id="t",
        channel="c",
        sender=new,
    )

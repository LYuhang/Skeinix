"""Live Playwright CDP clients bound to authenticated browser sessions.

The extension and controller WebSockets terminate in the API process. This
registry joins the two data planes without exposing a browser debug port. Its
key includes both the authoritative extension transport id and Chat channel so
one user's controller can never receive another Chat's CDP traffic.

The deployment must route both WebSockets for one browser lease to the same
controller worker. A distributed relay can replace this narrow registry without
changing the extension or Playwright protocol.
"""

from __future__ import annotations

from typing import Awaitable, Callable


ControllerSender = Callable[[dict], Awaitable[None]]


class PlaywrightControllerRegistry:
    def __init__(self) -> None:
        self._senders: dict[tuple[str, str], ControllerSender] = {}

    @staticmethod
    def key(transport_id: str, channel: str) -> tuple[str, str]:
        return (str(transport_id), str(channel))

    def register(
        self,
        *,
        transport_id: str,
        channel: str,
        send: ControllerSender,
    ) -> None:
        self._senders[self.key(transport_id, channel)] = send

    def unregister(
        self,
        *,
        transport_id: str,
        channel: str,
        sender: ControllerSender | None = None,
    ) -> bool:
        key = self.key(transport_id, channel)
        if sender is not None and self._senders.get(key) is not sender:
            return False
        return self._senders.pop(key, None) is not None

    async def forward_extension_message(
        self,
        *,
        transport_id: str,
        channel: str,
        message: dict,
    ) -> bool:
        sender = self._senders.get(self.key(transport_id, channel))
        if sender is None:
            return False
        try:
            await sender(message)
            return True
        except Exception:
            self.unregister(
                transport_id=transport_id,
                channel=channel,
                sender=sender,
            )
            return False


playwright_controllers = PlaywrightControllerRegistry()

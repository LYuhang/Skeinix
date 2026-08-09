"""Host-side send-command-and-await-observation primitive.

Both agent tools and engine nodes call
command_host.send_command(...). It mints a correlation id, ships the command over
the registry to the authenticated browser transport, and awaits the matching observation
the WS hub routes back via resolve_observation(). Media bytes in the observation
are turned into VFS paths (write_media, media.py) BEFORE the Observation is
returned, so a producer never sees raw bytes (§5.3)."""
from __future__ import annotations

import asyncio
import uuid
from typing import Callable

from .commands import Cmd, Observation, make_command, parse_observation
from .registry import TransportSendFailed, registry


class TransportClosed(Exception):
    pass


class CommandResultUnknown(Exception):
    """The command crossed an attempted-send boundary but has no observation."""


class CommandHost:
    def __init__(self, write_media: Callable[..., Observation] | None = None) -> None:
        # cid -> (future, owning event loop). The loop is stored so resolution can
        # be marshalled back onto the awaiting loop with call_soon_threadsafe — the
        # WS hub may resolve from a different loop/thread than the producer awaits on.
        self._pending: dict[str, tuple[asyncio.Future, asyncio.AbstractEventLoop]] = {}
        # Per-transport SERIAL lock: a browser drives ONE command at a time. The
        # agent may emit parallel tool calls (LangGraph runs them concurrently),
        # but interleaving commands over a single browser races — observations get
        # lost, DOM handle-stamps collide, a navigation mid-command derails the
        # next. Serializing per (tenant, browser) keeps browser execution strictly
        # sequential without constraining the model.
        self._locks: dict[str, asyncio.Lock] = {}
        # write_media(obs, *, transport_id) -> Observation with bytes replaced by paths
        self._write_media = write_media

    async def send_command(self, *, transport_id: str, channel: str, cmd: Cmd,
                           args: dict, target_id: str | None, producer: str,
                           timeout_s: float = 30.0,
                           write_media: Callable[..., Observation] | None = None) -> Observation:
        lock = self._locks.get(transport_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[transport_id] = lock
        async with lock:  # one in-flight command per browser
            cid = f"cmd_{uuid.uuid4().hex}"
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[cid] = (fut, loop)
            command_args = {**(args or {})}
            command_args.setdefault("command_id", cid)
            raw = make_command(cmd, id=cid, transport=transport_id, channel=channel,
                               args=command_args, target_id=target_id, producer=producer)
            try:
                try:
                    ok = await registry.send_to(transport_id, raw)
                except TransportSendFailed as exc:
                    raise CommandResultUnknown(
                        f"browser command delivery is uncertain for {transport_id}"
                    ) from exc
                if not ok:
                    raise TransportClosed(f"no live transport {transport_id}")
                obs: Observation = await asyncio.wait_for(fut, timeout=timeout_s)
            finally:
                self._pending.pop(cid, None)
        media_writer = write_media or self._write_media
        if obs.media and media_writer is not None:
            try:
                obs = media_writer(
                    obs,
                    transport_id=transport_id,
                    cmd=cmd,
                    args=command_args,
                )
            except TypeError:
                # Older tests/producers may provide a writer that only accepts
                # transport_id. Keep the host compatible while the canonical
                # writer consumes cmd/args for features such as save_path.
                obs = media_writer(obs, transport_id=transport_id)
        return obs

    def resolve_observation(self, raw: str) -> None:
        """Called by the WS hub for every inbound kind='observation' frame."""
        try:
            obs = parse_observation(raw)
        except ValueError:
            return
        entry = self._pending.get(obs.id)
        if entry is None:
            return
        fut, loop = entry

        def _set() -> None:
            if not fut.done():
                fut.set_result(obs)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _set()                       # same loop: resolve inline
        else:
            loop.call_soon_threadsafe(_set)  # cross-loop/thread: marshal back


command_host = CommandHost()  # write_media wired per-producer via media.host_media_writer

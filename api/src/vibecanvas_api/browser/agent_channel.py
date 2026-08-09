"""Agent-facing facade over ``command_host.send_command``.

``AgentBrowser`` does not define a second protocol. It binds one
``(transport_id, channel, producer)`` and exposes an async method per browser
command, mapping each Python name to the shared ``Cmd`` enum.

The facade also binds the host media writer to this
turn's chat workspace scope before each send, so any media BYTES in the returned
Observation are materialized into the chat workspace `/data` tier and substituted
with a PATH by the host (media.host_media_writer / write_observation_media). The
agent's tool outputs therefore stay strings (Global Constraint: media-as-path,
never bytes).

Each method returns the decoded Observation as a plain dict
``{"ok", "data", "media", "target_id", "error", "id"}`` — bytes never present.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from .commands import Cmd, Observation
from .host import command_host
from .media import host_media_writer


@dataclasses.dataclass(frozen=True)
class BrowserBinding:
    """The per-turn binding placed on ``AgentContext.browser`` for a browser turn.

    Holds only routing coordinates, not state or an open socket: the live
    ``transport_id`` for this (tenant, browser) and the ``channel`` (``chat:<id>``).
    ``build_agent_browser`` wraps it into an AgentBrowser bound to the run scope at
    tool-call time. Rebuilt each turn against the live transport."""
    transport_id: str
    channel: str


def _obs_to_dict(obs: Observation) -> dict:
    """Decode the typed Observation to a plain dict for tool output. Media slots
    already carry VFS paths (the host substituted them); no bytes are present."""
    if dataclasses.is_dataclass(obs):
        return dataclasses.asdict(obs)
    # Defensive: a fake/host that already returns a dict.
    return dict(obs)  # type: ignore[arg-type]


class AgentBrowser:
    """Bound to one (transport_id, channel) for a teaching turn. Stateless across
    turns; it is rebuilt each turn against the live browser transport."""

    def __init__(self, host=command_host, *, transport_id: str, channel: str,
                 workspace_scope_id: str, tenant_id: str | None,
                 producer: str = "agent", timeout_s: float = 30.0,
                 target_id: str | None = None) -> None:
        self._host = host
        self._transport_id = transport_id
        self._channel = channel
        self._workspace_scope_id = workspace_scope_id
        self._tenant_id = tenant_id
        self._producer = producer
        self._timeout_s = timeout_s
        self._target_id = target_id  # optional; empty = the controlled root tab

    async def _send(self, cmd: Cmd, **args: Any) -> dict:
        # Drop None args so the plugin sees only provided fields.
        clean = {k: v for k, v in args.items() if v is not None}
        # Pass the writer with this command. Never mutate the shared host: two
        # browser transports may run concurrently and must not overwrite each
        # other's chat workspace scope.
        media_writer = host_media_writer(
            workspace_scope_id=self._workspace_scope_id,
            tenant_id=self._tenant_id or "",
        )
        obs = await self._host.send_command(
            transport_id=self._transport_id, channel=self._channel, cmd=cmd,
            args=clean, target_id=self._target_id, producer=self._producer,
            timeout_s=self._timeout_s, write_media=media_writer)
        return _obs_to_dict(obs)

    # --- §5.1 command methods (thin; one line each) --------------------------
    # `tab` (a STABLE tabId) is the optional target for read/act/nav commands —
    # None means "the controlled tab". The extension resolves tab → live target.
    async def navigate(self, *, url, wait_until=None, timeout=None, tab=None): return await self._send(Cmd.NAVIGATE, url=url, wait_until=wait_until, timeout=timeout, tab=tab)
    async def snapshot(self, *, scope=None, prune=None, tab=None): return await self._send(Cmd.SNAPSHOT, scope=scope, prune=prune, tab=tab)
    async def read_text(self, *, selector=None, handle=None, query=None, context=None, max_chars=None, tab=None): return await self._send(Cmd.READ_TEXT, selector=selector, handle=handle, query=query, context=context, max_chars=max_chars, tab=tab)
    async def read_fields(self, *, selectors, tab=None): return await self._send(Cmd.READ_FIELDS, selectors=selectors, tab=tab)
    async def query(self, *, selector=None, role=None, name=None, text=None, tab=None): return await self._send(Cmd.QUERY, selector=selector, role=role, name=name, text=text, tab=tab)
    async def get_attribute(self, *, handle, name=None, tab=None): return await self._send(Cmd.GET_ATTRIBUTE, handle=handle, name=name, tab=tab)
    async def get_html(self, *, handle=None, selector=None, format=None, max_chars=None, tab=None): return await self._send(Cmd.GET_HTML, handle=handle, selector=selector, format=format, max_chars=max_chars, tab=tab)
    async def screenshot(self, *, scope=None, full_page=None, tab=None): return await self._send(Cmd.SCREENSHOT, scope=scope, full_page=full_page, tab=tab)
    async def get_image(self, *, handle, tab=None): return await self._send(Cmd.GET_IMAGE, handle=handle, tab=tab)
    async def acquire_video(self, *, handle, mode="auto", fps=None, max_frames=None, max_seconds=None, scale=None, tab=None): return await self._send(Cmd.ACQUIRE_VIDEO, handle=handle, mode=mode, fps=fps, max_frames=max_frames, max_seconds=max_seconds, scale=scale, tab=tab)
    async def fetch_resource(self, *, handle=None, selector=None, url=None, type=None, max_bytes=None, save_path=None, tab=None): return await self._send(Cmd.FETCH_RESOURCE, handle=handle, selector=selector, url=url, type=type, max_bytes=max_bytes, save_path=save_path, tab=tab)
    async def scroll(self, *, handle=None, tab=None): return await self._send(Cmd.SCROLL, handle=handle, tab=tab)
    async def wait_for(self, *, selector=None, text=None, timeout=None, tab=None): return await self._send(Cmd.WAIT_FOR, selector=selector, text=text, timeout=timeout, tab=tab)
    async def list_tabs(self): return await self._send(Cmd.LIST_TABS)
    async def list_open_tabs(self): return await self._send(Cmd.LIST_OPEN_TABS)
    async def use_tab(self, *, tab): return await self._send(Cmd.USE_TAB, tab=tab)
    async def switch_tab(self, *, tab): return await self._send(Cmd.SWITCH_TAB, tab=tab)
    async def close_tab(self, *, tab=None): return await self._send(Cmd.CLOSE_TAB, tab=tab)
    async def wait_for_new_tab(self, *, timeout=None): return await self._send(Cmd.WAIT_FOR_NEW_TAB, timeout=timeout)
    async def click(self, *, handle=None, selector=None, purpose=None, expect=None, tab=None): return await self._send(Cmd.CLICK, handle=handle, selector=selector, purpose=purpose, expect=expect, tab=tab)
    async def type(self, *, handle, text, replace=None, purpose=None, expect=None, tab=None): return await self._send(Cmd.TYPE, handle=handle, text=text, replace=replace, purpose=purpose, expect=expect, tab=tab)
    async def fill(self, *, handle, text, purpose=None, expect=None, tab=None): return await self._send(Cmd.FILL, handle=handle, text=text, purpose=purpose, expect=expect, tab=tab)
    async def select(self, *, handle, option, purpose=None, expect=None, tab=None): return await self._send(Cmd.SELECT, handle=handle, option=option, purpose=purpose, expect=expect, tab=tab)
    async def press(self, *, key, handle=None, purpose=None, expect=None, tab=None): return await self._send(Cmd.PRESS, key=key, handle=handle, purpose=purpose, expect=expect, tab=tab)
    async def submit(self, *, handle=None, purpose=None, expect=None, tab=None): return await self._send(Cmd.SUBMIT, handle=handle, purpose=purpose, expect=expect, tab=tab)
    async def assert_(self, *, condition, timeout=None, tab=None): return await self._send(Cmd.ASSERT, condition=condition, timeout=timeout, tab=tab)
    async def highlight(self, *, handle, label=None, decision=None): return await self._send(Cmd.HIGHLIGHT, handle=handle, label=label, decision=decision)
    async def narrate(self, *, text): return await self._send(Cmd.NARRATE, text=text)
    async def check_login(self, *, tab=None): return await self._send(Cmd.CHECK_LOGIN, tab=tab)
    async def start_session(self, *, target=None, tab=None, browser_session_id=None, session_generation=None): return await self._send(Cmd.START_SESSION, target=target, tab=tab, browser_session_id=browser_session_id, session_generation=session_generation)
    async def end_session(self, *, reason=None): return await self._send(Cmd.END_SESSION, reason=reason)


def build_agent_browser(ctx):
    """Build an AgentBrowser bound to this turn's transport + teaching scope, or
    None if the turn is not a transport-bound browser turn (tools soft-error
    "no_browser"). Wired off AgentContext in Task 4.

    ``ctx.browser`` carries the per-turn binding info. It may already BE an
    AgentBrowser (passed through), or a lightweight ``BrowserBinding`` holding
    (transport_id, channel) that we wrap with the run scope + run store here."""
    binding = getattr(ctx, "browser", None)
    if binding is None:
        return None
    if isinstance(binding, AgentBrowser):
        return binding
    transport_id = getattr(binding, "transport_id", None)
    channel = getattr(binding, "channel", None)
    if not transport_id or not channel:
        return None
    # Browser-mode media (screenshots/images) live in the current chat workspace
    # `/data/browser-media/...` path. In chat routes, ctx.wf_id is intentionally
    # the chat workspace scope id, while current_workflow_id separately tracks an
    # associated workflow. Fall back to chat_id only for isolated tests.
    workspace_scope_id = getattr(ctx, "wf_id", None) or getattr(ctx, "chat_id", None) or "nochat"
    return AgentBrowser(command_host, transport_id=transport_id, channel=channel,
                        workspace_scope_id=workspace_scope_id,
                        tenant_id=getattr(ctx, "tenant_id", None))

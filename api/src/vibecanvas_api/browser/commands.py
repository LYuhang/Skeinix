"""The shared browser-command vocabulary (R-C, §5.1).

THE single Python source of truth for the command set + arg/return shapes.
Both producers — agent tools and engine Read/Act nodes —
import from here; neither defines its own command list. A new command is added
once, here (and mirrored in extension/src/shared/commands.ts), and both
producers get it. The shared wire envelope remains unforked: a
command is kind="command" with data={cmd,args,target_id}; an observation is
kind="observation" with data={ok,target_id,error?,media?,...}.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .envelope import decode, encode


class Cmd(str, Enum):
    # --- Browser commands ---
    NAVIGATE = "navigate"
    SNAPSHOT = "snapshot"
    READ_TEXT = "read_text"
    READ_FIELDS = "read_fields"          # deterministic selector reads only (§5.1)
    QUERY = "query"
    GET_ATTRIBUTE = "get_attribute"
    GET_HTML = "get_html"
    SCREENSHOT = "screenshot"            # media → path
    GET_IMAGE = "get_image"             # media → path
    ACQUIRE_VIDEO = "acquire_video"     # media → path(s), tiered (§5.5)
    FETCH_RESOURCE = "fetch_resource"   # fetch URL via browser context → VFS path
    SCROLL = "scroll"
    WAIT_FOR = "wait_for"
    LIST_TABS = "list_tabs"              # "ls" for the controlled session's tabs
    SWITCH_TAB = "switch_tab"
    CLOSE_TAB = "close_tab"
    WAIT_FOR_NEW_TAB = "wait_for_new_tab"
    LIST_OPEN_TABS = "list_open_tabs"    # the user's OWN open tabs (to adopt one)
    USE_TAB = "use_tab"                  # adopt a user tab as the controlled root
    # --- Act (mutating) ---
    CLICK = "click"
    TYPE = "type"
    FILL = "fill"
    SELECT = "select"
    PRESS = "press"
    SUBMIT = "submit"
    # --- Verify ---
    ASSERT = "assert"
    # --- Overlay (teaching UX, cosmetic) ---
    HIGHLIGHT = "highlight"
    NARRATE = "narrate"
    # --- Session ---
    CHECK_LOGIN = "check_login"
    START_SESSION = "start_session"
    END_SESSION = "end_session"


READ_CMDS: frozenset[Cmd] = frozenset({
    Cmd.SNAPSHOT, Cmd.READ_TEXT, Cmd.READ_FIELDS, Cmd.QUERY,
    Cmd.GET_ATTRIBUTE, Cmd.GET_HTML, Cmd.SCREENSHOT, Cmd.GET_IMAGE,
    Cmd.ACQUIRE_VIDEO, Cmd.FETCH_RESOURCE, Cmd.SCROLL, Cmd.WAIT_FOR,
    Cmd.LIST_TABS, Cmd.WAIT_FOR_NEW_TAB, Cmd.LIST_OPEN_TABS,
})
ACT_CMDS: frozenset[Cmd] = frozenset({
    Cmd.CLICK, Cmd.TYPE, Cmd.FILL, Cmd.SELECT, Cmd.PRESS, Cmd.SUBMIT,
})
CONTROL_CMDS: frozenset[Cmd] = frozenset({
    Cmd.NAVIGATE, Cmd.SWITCH_TAB, Cmd.CLOSE_TAB, Cmd.USE_TAB,
    Cmd.START_SESSION, Cmd.END_SESSION,
})
VERIFY_CMDS: frozenset[Cmd] = frozenset({Cmd.ASSERT})
OVERLAY_CMDS: frozenset[Cmd] = frozenset({Cmd.HIGHLIGHT, Cmd.NARRATE})
SESSION_CMDS: frozenset[Cmd] = frozenset({Cmd.CHECK_LOGIN, Cmd.START_SESSION, Cmd.END_SESSION})

# The side-effect boundary is broader than form/page writes: navigation, tab
# selection/closing/adoption, and browser-session lifecycle also change browser
# state and must be treated as risk-bearing operations by authorization/audit.
MUTATING: frozenset[Cmd] = ACT_CMDS | CONTROL_CMDS

# Which observation slots a command returns as media bytes; the host turns each
# into a VFS path before the observation reaches a producer (§5.3).
MEDIA_SLOTS: dict[Cmd, tuple[str, ...]] = {
    Cmd.SCREENSHOT: ("screenshot",),
    Cmd.GET_IMAGE: ("image",),
    Cmd.ACQUIRE_VIDEO: ("frames", "video"),
    Cmd.FETCH_RESOURCE: ("resource",),
}


def make_command(cmd: Cmd, *, id: str, transport: str, channel: str,
                 args: dict, target_id: str | None, producer: str) -> str:
    """Build a kind='command' envelope. `target_id` is optional: an empty value
    means "the controlled root tab" — the extension's routeCommand resolves it to
    sm.knownTargets()[0] (§5.1 / the multi-tab session). Producers that care about
    a specific tab pass an explicit target_id."""
    if not isinstance(cmd, Cmd):
        raise ValueError(f"unknown command: {cmd!r}")
    return encode("command", id=id, channel=channel, transport=transport,
                  producer=producer,
                  data={"cmd": cmd.value, "args": args or {}, "target_id": target_id or ""})


@dataclass(frozen=True)
class Observation:
    id: str
    ok: bool
    data: dict
    media: list[dict]
    target_id: str | None
    error: str | None


def parse_observation(raw: str) -> Observation:
    """Parse a kind='observation' envelope into a typed Observation."""
    env = decode(raw)
    if env.get("kind") != "observation":
        raise ValueError(f"not an observation: kind={env.get('kind')!r}")
    data = env.get("data") or {}
    return Observation(
        id=env["id"],
        ok=bool(data.get("ok", False)),
        data=data,
        media=list(data.get("media") or []),
        target_id=data.get("target_id"),
        error=data.get("error"),
    )

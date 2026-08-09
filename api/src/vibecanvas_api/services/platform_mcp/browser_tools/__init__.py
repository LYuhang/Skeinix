"""The Platform MCP browser toolset — thin wrappers over the live browser.

Grouped by category (read / act / session / fetch), mirroring the shared browser
command vocabulary. Assembled into the agent's tool list by ``build_tools`` when
``browser`` is in active_modes. Browser mode is driven purely by ``/browser``
(side-panel-only, gated by the request ``surface`` at the routes layer) — there is
no feature flag.
"""
from __future__ import annotations

from .read import READ_TOOLS
from .act import ACT_TOOLS
from .session import SESSION_TOOLS
from .fetch_resource import FETCH_TOOLS

BROWSER_TOOLS = [*READ_TOOLS, *FETCH_TOOLS, *ACT_TOOLS, *SESSION_TOOLS]

__all__ = ["BROWSER_TOOLS", "READ_TOOLS", "FETCH_TOOLS", "ACT_TOOLS", "SESSION_TOOLS"]

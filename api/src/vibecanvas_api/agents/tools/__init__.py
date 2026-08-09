"""agents/tools — the agent's tool surface + the `build_tools` composer.

Mirror of the prompt composer (`agents/prompts/compose.build_system_prompt`):
ONE place that assembles the agent's tool list from `active_modes` (+ the
dynamic per-tenant groups), in a DETERMINISTIC order so the tool-schema prefix
is byte-stable when the inputs are unchanged (prefix-cache friendly).

Tool order (fixed for prefix-cache stability across turns):
  BASE        fs + todo + media + web_search + subagent + background controls
              + bash
  kb_tools    reserved; Knowledge Base access is Platform MCP
  meta_tools  reserved
  mcp_tools   connected MCP callable tools        (grow append-only; never shuffle)
  skill_tools (reserved: run_skill_script if re-introduced)

The reserved `meta_tools` slot sits BEFORE `mcp_tools` so connected callable
tools can grow without reshuffling the stable base prefix.
"""
from __future__ import annotations

from vibecanvas_api.agents.tools.background import BACKGROUND_TOOLS
from vibecanvas_api.agents.tools.fs import FS_TOOLS
from vibecanvas_api.agents.tools.media import MEDIA_TOOLS
from vibecanvas_api.agents.tools.sandbox import SANDBOX_TOOLS
from vibecanvas_api.agents.tools.subagent import SUBAGENT_TOOLS
from vibecanvas_api.agents.tools.todo import TODO_TOOLS
from vibecanvas_api.agents.tools.web import WEB_TOOLS


def build_tools(
    active_modes: set[str] | None = None,
    *,
    surface: str = "chat",
    kb_tools: list | tuple = (),
    meta_tools: list | tuple = (),
    mcp_tools: list | tuple = (),
    skill_tools: list | tuple = (),
    runtime_location: str = "host",
) -> list:
    """Assemble LangChain-private tools plus Runtime-loaded MCP tools.

    Order (fixed for prefix-cache stability):
        BASE (fs + todo + media + web_search + subagent + background controls
        + bash)
        + kb + meta + mcp + skill

    ``active_modes`` is accepted for the stable caller contract, but build and
    browser are cross-Runtime Platform MCP capabilities and are never registered
    here. The Runtime boundary selects their MCP descriptors.

    ``meta_tools``: reserved fixed slot
    BEFORE mcp_tools so connected MCP tools (which grow append-only) never shift
    the load tools' position in the schema — preserving the token prefix for all
    prior turns.
    """
    modes = set(active_modes or set())
    tools: list = [
        *FS_TOOLS,
        *TODO_TOOLS,
        *MEDIA_TOOLS,
        *WEB_TOOLS,
        *SUBAGENT_TOOLS,
        *BACKGROUND_TOOLS,
        *SANDBOX_TOOLS,
    ]
    # Cross-Runtime build/browser capabilities are supplied exclusively through
    # Platform MCP descriptors. ``active_modes`` affects descriptor selection,
    # never this LangChain-private tool registry.
    del modes, surface, runtime_location
    tools += [*kb_tools, *meta_tools, *mcp_tools, *skill_tools]
    return tools


def builtin_tool_names() -> set[str]:
    """Return the stable LangChain-private built-in names.

    An MCP server's tools must not shadow any name in this set. The dynamic
    connected-MCP group is excluded since it varies per turn.
    """
    static = {
        getattr(t, "name", "")
        for t in [
            *build_tools({"build", "browser"}),
            *build_tools({"build", "browser"}, surface="chat"),
            *build_tools({"build", "browser"}, surface="browser"),
        ]
    }
    return static

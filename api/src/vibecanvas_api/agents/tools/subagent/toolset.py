"""Tool set for the agent-as-tool subagent.

The subagent runs with a full copy of the main agent's ``AgentContext`` and
is granted a fixed subset of tool groups: fs + data + media + web + sandbox.
No MCP, no skill, no state, no build/canvas tools.
"""
from __future__ import annotations

from vibecanvas_api.services.agent_runtime.approval import (
    PRE_TOOL_APPROVAL_TOOLS,
)


SUBAGENT_DEFAULT_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "read_images",
    "web_search",
    "bash",
)

# ``render_interactive`` owns a post-tool interaction pause, while the
# Runtime-neutral registry contains the tools that may create a pre-tool
# approval request. Neither class belongs in an unattended Subagent.
SUBAGENT_FORBIDDEN_HITL_TOOL_NAMES: frozenset[str] = frozenset(
    {*PRE_TOOL_APPROVAL_TOOLS, "render_interactive"}
)


def build_agent_subagent_tools() -> list:
    """Fixed tool set for the agent-as-tool subagent.

    Grants the worker the same read/write/compute capabilities as the main
    agent minus anything that modifies the canvas or opens external services:

      fs      — read_file, write_file, edit_file, grep
      media   — read_images
      web     — web_search
      sandbox — bash (shell execution in the workflow's resident sandbox)

    Tabular work uses bash with Python/openpyxl instead of dedicated data tools.
    Excluded: state tools, build/canvas tools, browser, MCP, skill.
    """
    from vibecanvas_api.agents.tools.fs import FS_TOOLS
    from vibecanvas_api.agents.tools.media import MEDIA_TOOLS
    from vibecanvas_api.agents.tools.sandbox import bash
    from vibecanvas_api.agents.tools.web import WEB_TOOLS

    tools = [*FS_TOOLS, *MEDIA_TOOLS, *WEB_TOOLS, bash]
    names = tuple(str(getattr(item, "name", "")) for item in tools)
    if names != SUBAGENT_DEFAULT_TOOL_NAMES:
        raise RuntimeError(
            "Subagent default tool contract changed; review the fixed allowlist "
            f"before enabling it (actual={names!r})"
        )
    forbidden = set(names) & SUBAGENT_FORBIDDEN_HITL_TOOL_NAMES
    if forbidden:
        raise RuntimeError(
            "Subagent default tools must not create HITL requests: "
            + ", ".join(sorted(forbidden))
        )
    return tools

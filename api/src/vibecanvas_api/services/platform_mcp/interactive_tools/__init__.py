"""Cross-Runtime interactive artifact tools exposed through Platform MCP.

These tools are available on both the main chat surface and the browser
side-panel surface. They are not browser-control tools; they let the agent show
structured UI to the user inside the conversation transcript.
"""
from __future__ import annotations

from .render_interactive import render_interactive
from .render_url_preview import render_url_preview

INTERACTIVE_TOOLS = [render_interactive, render_url_preview]

__all__ = ["INTERACTIVE_TOOLS", "render_interactive", "render_url_preview"]

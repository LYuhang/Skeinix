"""agents/tools/media — Base-mode media tools (vision).

Currently the single ``read_images`` tool, which lets the agent actually SEE an
image. Part of the always-on Base toolset (assembled by ``build_tools``).
"""
from __future__ import annotations

from .read_images import read_images

MEDIA_TOOLS = [read_images]

__all__ = ["MEDIA_TOOLS", "read_images"]

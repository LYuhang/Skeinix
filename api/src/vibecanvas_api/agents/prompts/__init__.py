"""Modular prompt blocks (2026-06-19 redesign).

Each block is a small, self-contained named string in its own module. The
effective system prompt is composed from Base blocks only; active command blocks
are injected near their latest slash-command activation by CommandContextEdit.
Design principles:

  - Single source of truth: blocks carry IDENTITY + behavioral protocols + mode
    framing ONLY. They never list or describe tools — every tool self-describes
    via its own docstring (auto-injected by the agent framework).
  - Modular & incrementally composable: Base = IDENTITY + MEMORY + CONVERSATION
    + SURFACE; active commands (/workflow, /browser) are persistent command-context
    messages, not system prompt fragments.
"""

from .identity import IDENTITY
from .memory import MEMORY
from .conversation import CONVERSATION
from .surface import surface_prompt_for
from .workflow import WORKFLOW
from .browser import BROWSER
from .task import TASK
from .deployment import DEPLOYMENT
from .knowledge import KNOWLEDGE
from .diagram import DIAGRAM
from .document import DOCUMENT

__all__ = [
    "IDENTITY",
    "MEMORY",
    "CONVERSATION",
    "surface_prompt_for",
    "WORKFLOW",
    "BROWSER",
    "TASK",
    "DEPLOYMENT",
    "KNOWLEDGE",
    "DIAGRAM",
    "DOCUMENT",
]

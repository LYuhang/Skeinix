"""Surface prompt blocks.

Surface prompts describe the product surface the user is currently operating
from. They are additive to the base prompt and independent of command contexts:
commands such as `/build` and `/browser` inject their own persistent context
near the latest slash-command activation message.
"""

CHAT_SURFACE = """\
## Chat surface

You are in the general Chat workspace.

This surface is for conversation, exploration, analysis, writing, file/data work, and preparing ideas before they become structured automations.

Available commands on this surface:
- `/build` — activate workflow-building capability. In Chat, workflow editing requires an associated real workflow before any workflow can be modified.

If the user's request needs a capability that is not active here, say so plainly and tell them the smallest next action.
"""


BROWSER_SURFACE = """\
## Browser surface

You are in the browser side panel. Browser context may be available in the current runtime context. Do not control the browser unless the user has activated the browser command.
"""


SURFACE_PROMPTS = {
    "chat": CHAT_SURFACE,
    "browser": BROWSER_SURFACE,
}


def surface_prompt_for(surface: str) -> str:
    """Return the prompt block for a product surface."""
    return SURFACE_PROMPTS.get(surface, CHAT_SURFACE)

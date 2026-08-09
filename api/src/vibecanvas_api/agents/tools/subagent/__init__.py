"""agents/tools/subagent — delegate bounded sub-tasks to isolated worker agents."""
from vibecanvas_api.agents.tools.subagent.subagent import subagent

SUBAGENT_TOOLS = [subagent]

__all__ = ["subagent", "SUBAGENT_TOOLS"]

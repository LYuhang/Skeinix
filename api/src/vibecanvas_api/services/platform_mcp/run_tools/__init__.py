"""Platform MCP run/test tools (execute a workflow or one node).

NOT wired into the agent yet (deferred); kept here, ready to add.
"""
from vibecanvas_api.services.platform_mcp.run_tools.run_workflow import run_workflow
from vibecanvas_api.services.platform_mcp.run_tools.node_execute import node_execute
from vibecanvas_api.services.platform_mcp.run_tools.batch_execute import batch_execute

RUN_TOOLS = [run_workflow, node_execute, batch_execute]

__all__ = ["run_workflow", "node_execute", "batch_execute", "RUN_TOOLS"]

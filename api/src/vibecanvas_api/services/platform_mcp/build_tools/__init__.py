"""Platform MCP workflow-construction tools activated by ``/workflow``.

Each tool is a self-contained script in its own module. ``BUILD_TOOLS`` is the
group the composer adds when ``"workflow"`` is in active_modes.
"""
from vibecanvas_api.services.platform_mcp.build_tools.check_workflow import check_workflow
from vibecanvas_api.services.platform_mcp.build_tools.get_node_spec import get_node_spec
from vibecanvas_api.services.platform_mcp.build_tools.new_version import new_version
from vibecanvas_api.services.platform_mcp.build_tools.update_canvas import update_canvas

BUILD_TOOLS = [
    get_node_spec,
    check_workflow,
    update_canvas,
    new_version,
]

__all__ = [t.name for t in BUILD_TOOLS] + ["BUILD_TOOLS"]

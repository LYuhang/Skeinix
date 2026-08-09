"""Backend-selected MCP attachment behavior at the Agent Runtime boundary.

MCP tools are resolved and connected by the backend before the LangChain graph
is built. The Runtime consumes only the descriptors/tools supplied on the turn;
it must not reconnect an MCP from checkpointed names or tenant settings.
"""

from unittest.mock import patch

import pytest

from vibecanvas_api import agent as agent_mod


def _cfg():
    from vibecanvas_api.config import config as app_config

    return app_config.agent


@pytest.mark.asyncio
async def test_get_or_create_agent_has_no_mcp_without_runtime_attachment():
    with (
        patch.object(agent_mod, "_build_chat_model", return_value=object()),
        patch.object(agent_mod, "build_tools", return_value=[]) as build_tools,
        patch.object(agent_mod, "build_system_prompt", return_value=""),
        patch.object(agent_mod, "create_agent", return_value=object()),
    ):
        await agent_mod._get_or_create_agent(
            _cfg(), checkpointer=None, tenant_id=None
        )

    assert build_tools.call_args.kwargs["mcp_tools"] == []


@pytest.mark.asyncio
async def test_get_or_create_agent_uses_backend_attached_mcp_tools_and_catalog():
    runtime_tool = object()
    runtime_catalog = [{"name": "notion", "description": "Workspace search"}]
    with (
        patch.object(agent_mod, "_build_chat_model", return_value=object()),
        patch.object(agent_mod, "build_tools", return_value=[]) as build_tools,
        patch.object(
            agent_mod, "build_system_prompt", return_value=""
        ) as build_system_prompt,
        patch.object(agent_mod, "create_agent", return_value=object()),
    ):
        await agent_mod._get_or_create_agent(
            _cfg(),
            checkpointer=None,
            tenant_id=None,
            runtime_mcp_tools=[runtime_tool],
            runtime_mcp_catalog=runtime_catalog,
        )

    assert build_tools.call_args.kwargs["mcp_tools"] == [runtime_tool]
    assert build_system_prompt.call_args.kwargs["mcp_catalog"] == runtime_catalog

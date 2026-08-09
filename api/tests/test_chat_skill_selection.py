from unittest.mock import AsyncMock, patch

import pytest

from vibecanvas_api import agent as agent_mod


def _cfg():
    from vibecanvas_api.config import config as app_config

    return app_config.agent


@pytest.mark.asyncio
async def test_get_or_create_agent_forwards_runtime_skill_catalog_to_dynamic_tools():
    catalog = [
        {
            "name": "workflow-builder",
            "root_path": "/skills/skill-1",
        }
    ]
    dyn = {
        "mcp_catalog": [],
        "skill_catalog": catalog,
        "mcp_tools": [],
        "meta_tools": [],
        "skill_tools": [],
        "kb_tools": [],
    }
    with (
        patch.object(
            agent_mod, "_load_dynamic_tools", new=AsyncMock(return_value=dyn)
        ) as loader,
        patch.object(agent_mod, "create_agent", return_value=object()),
        patch.object(agent_mod, "_build_chat_model", return_value=object()),
    ):
        await agent_mod._get_or_create_agent(
            _cfg(),
            checkpointer=None,
            tenant_id="t",
            runtime_skill_catalog=catalog,
        )
    loader.assert_awaited_once_with(
        "t",
        runtime_mcp_tools=None,
        runtime_mcp_catalog=None,
        runtime_skill_catalog=catalog,
    )

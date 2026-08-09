"""Diagram Platform MCP tools re-authorize the concrete Chat resource."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vibecanvas_api.authorization.types import (
    Action,
    ConsistencyPreference,
)
from vibecanvas_api.services.platform_mcp import authorization
from vibecanvas_api.services.platform_mcp.diagram_tools.tools import (
    _sign_check,
    _verify_check,
)
from vibecanvas_api.agents.tools.decorator import ToolError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "expected_action"),
    [
        ("inspect_diagram", Action.VIEW),
        ("check_diagram", Action.UPDATE),
        ("review_diagram", Action.VIEW),
        ("present_diagram", Action.UPDATE),
        ("export_diagram", Action.UPDATE),
    ],
)
async def test_diagram_tools_use_fresh_chat_authorization(
    monkeypatch,
    tool_name,
    expected_action,
):
    require_chat_action = AsyncMock()
    monkeypatch.setattr(
        authorization,
        "require_chat_action",
        require_chat_action,
    )
    ctx = SimpleNamespace(chat_id="chat-1", tenant_id="tenant-1")

    await authorization.prepare_platform_tool(
        ctx,
        server="diagram",
        tool_name=tool_name,
        arguments={},
    )

    require_chat_action.assert_awaited_once_with(
        ctx,
        expected_action,
        consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["get_diagram_spec", "search_diagram_assets"],
)
async def test_static_diagram_catalog_tools_need_no_resource_check(
    monkeypatch,
    tool_name,
):
    require_chat_action = AsyncMock()
    monkeypatch.setattr(
        authorization,
        "require_chat_action",
        require_chat_action,
    )

    await authorization.prepare_platform_tool(
        SimpleNamespace(),
        server="diagram",
        tool_name=tool_name,
        arguments={},
    )

    require_chat_action.assert_not_awaited()


def test_check_ref_is_bound_to_chat_turn_workspace_and_runtime_session():
    ctx = SimpleNamespace(
        tenant_id="tenant-1",
        username="user-1",
        chat_id="chat-1",
        wf_id="workspace-1",
        turn_id="turn-1",
        runtime_session_id="runtime-1",
    )
    claims = {
        "tenant": ctx.tenant_id,
        "user": ctx.username,
        "chat": ctx.chat_id,
        "workspace": ctx.wf_id,
        "turn": ctx.turn_id,
        "runtime_session": ctx.runtime_session_id,
        "exp": int(
            (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
        ),
    }
    signed = _sign_check(claims)
    assert _verify_check(signed, ctx) == claims

    for field in (
        "tenant_id",
        "username",
        "chat_id",
        "wf_id",
        "turn_id",
        "runtime_session_id",
    ):
        other = SimpleNamespace(**vars(ctx))
        setattr(other, field, "other")
        with pytest.raises(ToolError) as error:
            _verify_check(signed, other)
        assert str(error.value) == "check_scope_mismatch"

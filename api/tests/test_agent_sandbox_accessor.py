import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from vibecanvas_api.agent import AgentContext


@pytest.mark.asyncio
async def test_sandbox_session_lazily_resolves_manager():
    ctx = AgentContext(wf_id="w1", tenant_id="t1")
    fake_session = MagicMock()
    fake_mgr = MagicMock()
    fake_mgr.get_session = AsyncMock(return_value=fake_session)
    with patch("vibecanvas_api.agent.get_sandbox_manager", return_value=fake_mgr):
        s = await ctx.sandbox_session()
    assert s is fake_session
    fake_mgr.get_session.assert_awaited_once_with(
        "t1", "w1", None, expose_run=True,
    )


@pytest.mark.asyncio
async def test_platform_mcp_borrows_loaded_runtime_session_without_rebuild():
    """Host Platform MCP tools share the exact Runtime-owned sandbox.

    Codex sessions have a private Runtime mount. Resolving the same workspace
    through the ordinary no-Runtime shape would rebuild the cache entry and let
    the retired session overwrite a newer host-side VFS commit on close.
    """
    ctx = AgentContext(
        wf_id="chat-workspace",
        tenant_id="tenant-a",
        runtime_location="platform_mcp",
    )
    loaded = MagicMock()
    fake_mgr = MagicMock()
    fake_mgr.get_loaded_session = AsyncMock(return_value=loaded)
    fake_mgr.get_session = AsyncMock(
        side_effect=AssertionError("Platform MCP must not rebuild the sandbox")
    )

    with patch("vibecanvas_api.agent.get_sandbox_manager", return_value=fake_mgr):
        first = await ctx.sandbox_session()
        second = await ctx.sandbox_session()

    assert first is loaded and second is loaded
    fake_mgr.get_loaded_session.assert_awaited_once_with(
        "tenant-a", "chat-workspace"
    )
    fake_mgr.get_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_mcp_fails_closed_without_active_runtime_session():
    ctx = AgentContext(
        wf_id="chat-workspace",
        tenant_id="tenant-a",
        runtime_location="platform_mcp",
    )
    fake_mgr = MagicMock()
    fake_mgr.get_loaded_session = AsyncMock(return_value=None)
    fake_mgr.get_session = AsyncMock()

    with patch("vibecanvas_api.agent.get_sandbox_manager", return_value=fake_mgr):
        with pytest.raises(RuntimeError, match="active Runtime sandbox"):
            await ctx.sandbox_session()

    fake_mgr.get_session.assert_not_awaited()

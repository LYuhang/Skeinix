from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import types

from vibecanvas_api.auth.deps import AuthContext
from vibecanvas_api.services.platform_mcp import server as platform_server
from vibecanvas_api.services.platform_mcp.capability import platform_mcp_policy
from vibecanvas_api.services.platform_mcp.server import (
    BUILD_MCP,
    CONFIG_MCP,
    DEPLOYMENT_MCP,
    DIAGRAM_MCP,
    INTERACTIVE_MCP,
    KNOWLEDGE_MCP,
    PLAN_MCP,
    TASK_MCP,
    WORKFLOW_MCP,
    _tool_error_result,
)
from vibecanvas_api.storage.sync_session import current_sync_tenant_id


def _capability(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "organization_id": "tenant-a",
        "user_id": "user-a",
        "chat_id": "chat-a",
        "turn_id": "turn-a",
        "workspace_scope_id": "workspace-a",
        "runtime_session_id": "runtime-a",
        "session_id": "session-a",
        "session_generation": 3,
        "membership_id": "membership-a",
        "authorization_generation": "generation-a",
        "approval_mode": "agent",
        "server": "workflow",
        "actions": (
            "chat:execute",
            "platform_mcp:call",
            "workflow:view",
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _identity():
    return AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        email="user-a@example.com",
        active_organization_id="tenant-a",
        membership_id="membership-a",
        membership_role="member",
        membership_status="active",
        session_id="session-a",
        session_generation=3,
        authentication_strength="password",
    )


def _allow_context_checks(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_server,
        "_live_platform_identity",
        AsyncMock(return_value=_identity()),
    )

    class AllowService:
        async def check(self, *_args, **_kwargs):
            return SimpleNamespace(allowed=True)

    monkeypatch.setattr(
        platform_server,
        "authz_service_for_session",
        lambda **_kwargs: AllowService(),
    )


async def _tools(server) -> dict[str, types.Tool]:
    handler = server._mcp_server.request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))
    return {tool.name: tool for tool in result.root.tools}


def test_tool_error_result_is_model_correctable_mcp_error() -> None:
    result = _tool_error_result(
        code="invalid_tool_arguments",
        message="'ref' is a required property",
        tool_name="present_diagram",
        json_pointer="/ref",
    )

    assert result.isError is True
    assert len(result.content) == 1
    assert isinstance(result.content[0], types.TextContent)
    payload = json.loads(result.content[0].text)
    assert payload == {
        "status": "error",
        "error": {
            "code": "invalid_tool_arguments",
            "message": "'ref' is a required property",
            "json_pointer": "/ref",
        },
        "retry": {
            "tool": "present_diagram",
            "instruction": (
                "Correct only the rejected argument using this tool's "
                "published inputSchema and exact previously returned values."
            ),
        },
    }


@pytest.mark.asyncio
async def test_management_catalog_matches_every_live_platform_registry() -> None:
    """The settings UI must not drift from the MCP services agents receive."""
    servers = {
        "config": CONFIG_MCP,
        "interactive": INTERACTIVE_MCP,
        "workflow": WORKFLOW_MCP,
        "task": TASK_MCP,
        "deployment": DEPLOYMENT_MCP,
        "knowledge": KNOWLEDGE_MCP,
        "build": BUILD_MCP,
        "plan": PLAN_MCP,
        "diagram": DIAGRAM_MCP,
    }
    catalog = {item["id"]: item for item in platform_server.platform_mcp_catalog()}

    assert set(catalog) == set(servers)
    for server_name, server in servers.items():
        registered = await _tools(server)
        listed = {tool["name"]: tool for tool in catalog[server_name]["tools"]}
        assert set(listed) == set(registered)
        assert all(tool["input_schema"]["type"] == "object" for tool in listed.values())

    assert catalog["plan"]["runtime_types"] == ["langchain"]
    assert catalog["workflow"]["activation_mode"] == "base"
    assert catalog["task"]["activation"] == "/task"
    assert catalog["diagram"]["runtime_types"] == ["langchain", "codex"]


@pytest.mark.asyncio
async def test_workflow_discovery_is_default_and_build_mutations_are_separate() -> None:
    tools = await _tools(WORKFLOW_MCP)
    assert set(tools) == {"list_workflows", "get_workflow"}
    assert all(tool.annotations.readOnlyHint for tool in tools.values())

    tools = await _tools(BUILD_MCP)
    assert set(tools) == {
        "set_workflow",
        "create_workflow",
        "get_node_spec",
        "check_workflow",
        "update_canvas",
        "new_version",
        "run_workflow",
        "node_execute",
        "batch_execute",
    }
    assert tools["update_canvas"].inputSchema["properties"]["require_valid"]["default"] is True
    # Platform authorization is decided from concrete CallTool arguments by
    # the backend-owned HITL protocol, not by a Runtime-specific MCP hint.
    assert tools["update_canvas"].annotations.destructiveHint is False
    assert tools["run_workflow"].annotations.readOnlyHint is False
    assert tools["batch_execute"].inputSchema["properties"]["row_concurrency"]["default"] == 4


@pytest.mark.asyncio
async def test_task_and_deployment_platform_mcps_export_crud_contracts() -> None:
    task_tools = await _tools(TASK_MCP)
    assert set(task_tools) == {
        "task_list",
        "task_get",
        "task_create_scheduled_run",
        "task_update_scheduled_run",
        "task_delete_scheduled_run",
        "task_cancel",
        "task_resume",
    }
    assert task_tools["task_list"].annotations.readOnlyHint is True
    assert (
        task_tools["task_create_scheduled_run"]
        .inputSchema["properties"]["require_user_auth"]["default"]
        is True
    )
    assert task_tools["task_delete_scheduled_run"].annotations.destructiveHint is True

    deployment_tools = await _tools(DEPLOYMENT_MCP)
    assert set(deployment_tools) == {
        "deployment_list",
        "deployment_get",
        "deployment_create",
        "deployment_update",
        "deployment_delete",
    }
    assert deployment_tools["deployment_get"].annotations.readOnlyHint is True
    assert (
        deployment_tools["deployment_create"]
        .inputSchema["properties"]["require_user_auth"]["default"]
        is True
    )
    assert deployment_tools["deployment_delete"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_knowledge_platform_mcp_exports_read_only_contract() -> None:
    tools = await _tools(KNOWLEDGE_MCP)
    assert set(tools) == {
        "list_knowledge_bases",
        "get_knowledge_base",
        "list_knowledge_files",
        "search_knowledge",
        "read_knowledge_file",
    }
    assert all(tool.annotations.readOnlyHint for tool in tools.values())
    assert (
        tools["search_knowledge"]
        .inputSchema["properties"]["top_k"]["default"]
        == 5
    )


@pytest.mark.asyncio
async def test_plan_platform_mcp_exports_only_create_from_file_contract() -> None:
    tools = await _tools(PLAN_MCP)
    assert set(tools) == {"create_execution_plan"}
    create = tools["create_execution_plan"]
    assert set(create.inputSchema["properties"]) == {"plan_path"}
    assert create.annotations.readOnlyHint is False


@pytest.mark.asyncio
async def test_diagram_platform_mcp_exports_closed_loop_contract() -> None:
    tools = await _tools(DIAGRAM_MCP)
    assert set(tools) == {
        "get_diagram_spec",
        "search_diagram_assets",
        "inspect_diagram",
        "check_diagram",
        "review_diagram",
        "read_diagram_review_image",
        "export_diagram",
    }
    assert tools["get_diagram_spec"].annotations.readOnlyHint is True
    assert tools["check_diagram"].annotations.readOnlyHint is False
    assert tools["review_diagram"].annotations.readOnlyHint is False
    assert tools["read_diagram_review_image"].annotations.readOnlyHint is True
    assert tools["export_diagram"].annotations.idempotentHint is True
    for tool in tools.values():
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert tool.outputSchema["additionalProperties"] is False
        assert all(
            heading in (tool.description or "")
            for heading in (
                "Use when:",
                "Do not use:",
                "Input comes from:",
                "On success:",
                "On recoverable result:",
            )
        )
    check_schema = tools["check_diagram"].inputSchema
    assert check_schema["properties"]["source_ref"][
        "additionalProperties"
    ] is False
    assert check_schema["properties"]["spec_ref"][
        "additionalProperties"
    ] is False
    assert len(
        tools["review_diagram"].inputSchema["properties"]["focus"]["oneOf"]
    ) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("server_name", "server"),
    [
        ("config", CONFIG_MCP),
        ("interactive", INTERACTIVE_MCP),
        ("workflow", WORKFLOW_MCP),
        ("task", TASK_MCP),
        ("deployment", DEPLOYMENT_MCP),
        ("knowledge", KNOWLEDGE_MCP),
        ("build", BUILD_MCP),
        ("plan", PLAN_MCP),
        ("diagram", DIAGRAM_MCP),
    ],
)
async def test_every_registered_platform_tool_has_a_signed_action_ceiling(
    server_name: str,
    server,
) -> None:
    policy = platform_mcp_policy(
        organization_id="tenant-a",
        chat_id="chat-a",
        workspace_scope_id="workspace-a",
        server=server_name,
    )
    capability = _capability(
        server=server_name,
        actions=policy.actions,
    )
    for tool_name in await _tools(server):
        platform_server._require_tool_capability(
            capability,
            server=server_name,
            tool_name=tool_name,
        )


@pytest.mark.asyncio
async def test_platform_tool_invocation_scopes_and_restores_sync_tenant(monkeypatch) -> None:
    capability = _capability()
    context = SimpleNamespace(pending_vibe=None, workflow={})

    async def fake_context(_capability):
        assert current_sync_tenant_id.get() == "tenant-a"
        return context

    async def coroutine(*, runtime, value):
        assert runtime.context is context
        assert current_sync_tenant_id.get() == "tenant-a"
        return value, {
            "schema_version": 1,
            "status": "success",
            "meta": {"tool": "example"},
        }

    monkeypatch.setattr(platform_server, "_capability", lambda _server: capability)
    monkeypatch.setattr(platform_server, "_context_for", fake_context)
    monkeypatch.setattr(
        platform_server,
        "prepare_platform_tool",
        AsyncMock(),
    )
    tool = SimpleNamespace(name="get_workflow", coroutine=coroutine)

    outer = current_sync_tenant_id.set("outer-tenant")
    try:
        content, artifact = await platform_server._invoke(
            tool, {"value": "ok"}, "workflow"
        )
        assert content[0].text == "ok"
        assert artifact["status"] == "success"
        assert current_sync_tenant_id.get() == "outer-tenant"
    finally:
        current_sync_tenant_id.reset(outer)


@pytest.mark.asyncio
async def test_platform_tool_error_restores_sync_tenant(monkeypatch) -> None:
    capability = _capability(tenant_id="tenant-b", organization_id="tenant-b")
    context = SimpleNamespace(pending_vibe=None, workflow={})

    async def coroutine(*, runtime):
        return "invalid workflow", {
            "schema_version": 1,
            "status": "error",
            "meta": {"tool": "check_workflow"},
        }

    monkeypatch.setattr(platform_server, "_capability", lambda _server: capability)

    async def fake_context(_capability):
        return context

    monkeypatch.setattr(platform_server, "_context_for", fake_context)
    monkeypatch.setattr(
        platform_server,
        "prepare_platform_tool",
        AsyncMock(),
    )
    tool = SimpleNamespace(name="get_workflow", coroutine=coroutine)

    outer = current_sync_tenant_id.set("outer-tenant")
    try:
        result = await platform_server._invoke(tool, {}, "workflow")
        assert isinstance(result, types.CallToolResult)
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["error"] == {
            "code": "platform_tool_failed",
            "message": "invalid workflow",
        }
        assert current_sync_tenant_id.get() == "outer-tenant"
    finally:
        current_sync_tenant_id.reset(outer)


@pytest.mark.asyncio
@pytest.mark.parametrize("run_status", ["waiting_approval", "cancel_requested", "completed"])
async def test_platform_context_rejects_non_running_turn(
    monkeypatch, run_status: str
) -> None:
    capability = _capability()

    @asynccontextmanager
    async def fake_session_scope(**_kwargs):
        yield object()

    class FakeRunsRepo:
        def __init__(self, _session):
            pass

        async def get_for_chat(self, *_args, **_kwargs):
            return SimpleNamespace(status=run_status)

    monkeypatch.setattr(platform_server, "session_scope", fake_session_scope)
    monkeypatch.setattr(platform_server, "AgentRunsRepo", FakeRunsRepo)
    _allow_context_checks(monkeypatch)

    with pytest.raises(PermissionError, match="active run"):
        await platform_server._context_for(capability)


@pytest.mark.asyncio
async def test_platform_context_rejects_workspace_scope_mismatch(monkeypatch) -> None:
    capability = _capability(workspace_scope_id="workspace-from-token")

    @asynccontextmanager
    async def fake_session_scope(**_kwargs):
        yield object()

    class FakeRunsRepo:
        def __init__(self, _session):
            pass

        async def get_for_chat(self, *_args, **_kwargs):
            return SimpleNamespace(status="running")

    class FakeChatRepo:
        def __init__(self, _session, _user_id):
            pass

        async def get_platform_context_binding(self, _chat_id):
            return {
                "carrier_scope_id": "carrier-from-database",
                "runtime_session_id": "runtime-a",
                "current_workflow_id": None,
            }

    monkeypatch.setattr(platform_server, "session_scope", fake_session_scope)
    monkeypatch.setattr(platform_server, "AgentRunsRepo", FakeRunsRepo)
    monkeypatch.setattr(platform_server, "ChatRepo", FakeChatRepo)
    _allow_context_checks(monkeypatch)
    monkeypatch.setattr(
        platform_server,
        "chat_workspace_scope_id",
        lambda _chat_id: "workspace-from-database",
    )

    with pytest.raises(PermissionError, match="workspace"):
        await platform_server._context_for(capability)


@pytest.mark.asyncio
async def test_platform_context_rebuilds_active_membership_without_preloading_workflow(
    monkeypatch,
) -> None:
    capability = _capability()

    @asynccontextmanager
    async def fake_session_scope(**_kwargs):
        yield object()

    class FakeRunsRepo:
        def __init__(self, _session):
            pass

        async def get_for_chat(self, *_args, **_kwargs):
            return SimpleNamespace(status="running")

    class FakeChatRepo:
        def __init__(self, _session, _user_id):
            pass

        async def get_platform_context_binding(self, _chat_id):
            return {
                "carrier_scope_id": "carrier-a",
                "runtime_session_id": "runtime-a",
                "current_workflow_id": "workflow-a",
            }

    class FakeWorkflowRepo:
        def __init__(self, _user_id):
            pass

        def get_current_workflow(self, _workflow_id):
            raise AssertionError("workflow content was loaded before authorization")

    monkeypatch.setattr(platform_server, "session_scope", fake_session_scope)
    monkeypatch.setattr(platform_server, "AgentRunsRepo", FakeRunsRepo)
    monkeypatch.setattr(platform_server, "ChatRepo", FakeChatRepo)
    _allow_context_checks(monkeypatch)
    monkeypatch.setattr(platform_server, "SyncWorkflowRepo", FakeWorkflowRepo)
    monkeypatch.setattr(
        platform_server,
        "chat_workspace_scope_id",
        lambda _chat_id: "workspace-a",
    )

    context = await platform_server._context_for(capability)

    assert context.workflow == {}
    assert context.current_workflow_id == "workflow-a"
    assert context.authorization_membership_id == "membership-a"
    assert context.authorization_membership_role == "member"
    assert context.authorization_membership_status == "active"
    assert context.authorization_session_id == "session-a"
    assert context.authorization_session_generation == 3
    assert context.runtime_session_id == "runtime-a"

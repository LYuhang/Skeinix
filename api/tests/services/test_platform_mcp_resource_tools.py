from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from langchain.tools import ToolRuntime

from vibecanvas_api.services.platform_mcp.resource_tools import (
    deployment_create,
    deployment_delete,
    deployment_list,
    deployment_update,
    get_knowledge_base,
    list_knowledge_bases,
    list_knowledge_files,
    read_knowledge_file,
    search_knowledge,
    task_create_scheduled_run,
    task_delete_scheduled_run,
    task_list,
    task_update_scheduled_run,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models_kb import KbChunk
from vibecanvas_api.storage.repo_kb import KbRepo


async def _register(client) -> tuple[dict[str, str], dict]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"platform_mcp_{uuid.uuid4().hex[:12]}@example.com",
            "username": "Platform MCP",
            "password": "pw12345678",
        },
    )
    assert response.status_code in (200, 201), response.text
    headers = {"Authorization": f"Bearer {response.json()['session_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return headers, me


def _runtime(me: dict, *, authorization_client) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=SimpleNamespace(
            username=me["user_id"],
            tenant_id=me["tenant_id"],
            turn_id="resource-tools-turn",
            authorization_client=authorization_client,
            authorization_membership_id="resource-tools-membership",
            authorization_membership_role="owner",
            authorization_membership_status="active",
            authorization_session_generation=1,
            authorization_authentication_strength="test",
        ),
        config={"configurable": {"thread_id": "resource-tools"}},
        stream_writer=lambda _chunk: None,
        tool_call_id="resource-tools",
        store=None,
        tools=[],
    )


async def _workflow(client, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"name": "Platform MCP workflow"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["wf_id"]


@pytest.mark.asyncio
async def test_knowledge_platform_mcp_uses_platform_database(
    client,
    monkeypatch,
) -> None:
    headers, me = await _register(client)
    created = await client.post(
        "/api/v1/kb",
        headers=headers,
        json={"name": "Platform knowledge"},
    )
    assert created.status_code == 201, created.text
    knowledge_base_id = created.json()["id"]
    runtime = _runtime(
        me,
        authorization_client=client._transport.app.state.openfga_client,
    )

    content, artifact = await list_knowledge_bases.coroutine(
        runtime=runtime,
    )
    listed = json.loads(content)
    assert artifact["status"] == "success"
    assert [item["id"] for item in listed] == [knowledge_base_id]
    assert listed[0]["access"]["effective_role"] == "manager"

    content, _ = await get_knowledge_base.coroutine(
        kb_id=knowledge_base_id,
        runtime=runtime,
    )
    assert json.loads(content)["id"] == knowledge_base_id

    async with session_scope(tenant_id=me["tenant_id"]) as database:
        repo = KbRepo(database)
        knowledge = await repo.get_active(uuid.UUID(knowledge_base_id))
        assert knowledge is not None
        source = await repo.create_file(
            kb_id=knowledge.id,
            tenant_id=uuid.UUID(me["tenant_id"]),
            user_id=uuid.UUID(me["user_id"]),
            name="handbook.md",
            parser_type="markdown",
            mime_type="text/markdown",
            file_size=20,
            content_hash="a" * 64,
            status="indexed",
        )
        await repo.bulk_insert_chunks([KbChunk(
            file_id=source.id,
            kb_id=knowledge.id,
            tenant_id=uuid.UUID(me["tenant_id"]),
            chunk_index=0,
            text="The exact handbook identifier is AGENTIC_FOLDER_OK.",
            chunk_metadata={"heading": "Verification"},
        )])
        await database.commit()
        source_id = str(source.id)

    content, _ = await list_knowledge_files.coroutine(
        kb_id=knowledge_base_id,
        runtime=runtime,
    )
    folder = json.loads(content)
    assert folder["virtual_root"] == f"/knowledge/{knowledge_base_id}"
    assert folder["files"][0]["virtual_path"].endswith("/handbook.md")

    content, _ = await read_knowledge_file.coroutine(
        kb_id=knowledge_base_id,
        file_id=source_id,
        runtime=runtime,
    )
    page = json.loads(content)
    assert page["has_more"] is False
    assert page["chunks"][0]["text"].endswith("AGENTIC_FOLDER_OK.")

    content, _ = await search_knowledge.coroutine(
        kb_ids=[knowledge_base_id],
        query="anything",
        top_k=5,
        runtime=runtime,
    )
    results = json.loads(content)["results"]
    assert results == []


@pytest.mark.asyncio
async def test_task_platform_mcp_crud_uses_platform_database(client) -> None:
    headers, me = await _register(client)
    workflow_id = await _workflow(client, headers)
    runtime = _runtime(
        me,
        authorization_client=client._transport.app.state.openfga_client,
    )

    content, artifact = await task_create_scheduled_run.coroutine(
        name="Hourly review",
        workflow_id=workflow_id,
        schedule_type="interval",
        interval_seconds=3600,
        require_user_auth=True,
        runtime=runtime,
    )
    created = json.loads(content)
    task_id = created["task"]["id"]
    assert artifact["status"] == "success"
    assert created["schedule"]["name"] == "Hourly review"

    content, _ = await task_update_scheduled_run.coroutine(
        task_id=task_id,
        name="Daily review",
        enabled=False,
        require_user_auth=True,
        runtime=runtime,
    )
    assert json.loads(content)["schedule"]["name"] == "Daily review"

    content, _ = await task_list.coroutine(
        workflow_id=workflow_id,
        limit=20,
        offset=0,
        runtime=runtime,
    )
    listed = json.loads(content)
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == task_id
    assert listed["has_more"] is False

    content, _ = await task_delete_scheduled_run.coroutine(
        task_id=task_id,
        require_user_auth=True,
        runtime=runtime,
    )
    assert json.loads(content) == {"status": "deleted"}


@pytest.mark.asyncio
async def test_deployment_platform_mcp_crud_uses_platform_database(client) -> None:
    headers, me = await _register(client)
    workflow_id = await _workflow(client, headers)
    runtime = _runtime(
        me,
        authorization_client=client._transport.app.state.openfga_client,
    )
    slug = f"mcp-{uuid.uuid4().hex[:12]}"

    content, artifact = await deployment_create.coroutine(
        workflow_id=workflow_id,
        name="Agent deployment",
        slug=slug,
        trigger_type="api",
        version_pin="head",
        require_user_auth=True,
        runtime=runtime,
    )
    created = json.loads(content)
    deployment_id = created["id"]
    assert artifact["status"] == "success"
    assert created["api_key"]

    content, _ = await deployment_update.coroutine(
        deployment_id=deployment_id,
        name="Updated deployment",
        enabled=False,
        require_user_auth=True,
        runtime=runtime,
    )
    assert json.loads(content)["name"] == "Updated deployment"

    content, _ = await deployment_list.coroutine(
        workflow_id=workflow_id,
        limit=20,
        offset=0,
        runtime=runtime,
    )
    listed = json.loads(content)
    assert listed["items"][0]["id"] == deployment_id
    assert "api_key_hash" not in listed["items"][0]

    content, _ = await deployment_delete.coroutine(
        deployment_id=deployment_id,
        require_user_auth=True,
        runtime=runtime,
    )
    assert json.loads(content) == {"status": "deleted"}

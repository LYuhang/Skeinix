from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from vibecanvas_api.storage.agent_runs_repo import AgentRunsRepo
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.hitl_repo import HitlRepo


async def _register(client) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"hitl_{uuid.uuid4().hex[:12]}@example.com",
            "username": "HITL User",
            "password": "pw12345678",
        },
    )
    assert response.status_code in (200, 201), response.text
    token = response.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return token, me


async def _create_run(
    me: dict,
    *,
    chat_id: str,
    run_id: str,
    client_request_id: str,
) -> None:
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        await AgentRunsRepo(session).create(
            run_id=run_id,
            tenant_id=me["tenant_id"],
            chat_id=chat_id,
            creator_user_id=me["user_id"],
            client_request_id=client_request_id,
            input_snapshot={},
        )


async def _seed_chat(
    me: dict,
    *,
    scope_id: str,
    chat_id: str,
    surface: str,
) -> None:
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        await ChatRepo(session, me["user_id"]).register_session(
            scope_id,
            chat_id=chat_id,
            surface=surface,
        )


@pytest.mark.asyncio
async def test_interactive_artifact_resource_draft_and_result_file_are_chat_scoped(
    client, app_engine, monkeypatch,
):
    mirrored: list[tuple[str, str, str, bytes]] = []

    class SandboxManager:
        async def mirror_vfs_write(self, tenant_id, scope_id, path, data):
            mirrored.append((tenant_id, scope_id, path, data))

    monkeypatch.setattr(
        "vibecanvas_api.routes.chats.get_sandbox_manager",
        lambda: SandboxManager(),
    )
    token, me = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}
    boot = await client.get("/api/v1/chats/bootstrap?surface=chat", headers=headers)
    scope_id = boot.json()["carrier_scope_id"]
    chat_id = "c_interactive_resource"
    artifact_id = "ia_interactive_resource"

    await _seed_chat(me, scope_id=scope_id, chat_id=chat_id, surface="chat")

    async with session_scope(tenant_id=me["tenant_id"]) as session:
        await HitlRepo(session).create_interactive_artifact(
            artifact_id=artifact_id,
            tenant_id=me["tenant_id"],
            chat_id=chat_id,
            run_id=None,
            component_type="html_preview",
            completion_mode="wait_for_submit",
            title="Dataset review",
            definition_json={
                "kind": "interactive_artifact",
                "component_type": "html_preview",
                "props": {
                    "html": '<img src="/mount/data/data_list.jsonl">',
                },
            },
            artifact_ref=None,
            content_hash=None,
        )

    session_response = await client.post(
        f"/api/v1/interactive-artifacts/{artifact_id}/resource-session",
        headers=headers,
    )
    assert session_response.status_code == 200, session_response.text
    session = session_response.json()
    mounts = {item["path_prefix"]: item["root_url"] for item in session["resource_mounts"]}
    assert set(mounts) == {"/", "/mount/"}
    assert all(me["user_id"] not in url for url in mounts.values())
    assert all(chat_id not in url for url in mounts.values())
    assert mounts["/mount/"] != mounts["/"]
    assert mounts["/mount/"].endswith("/mount/")
    assert session["base_url"].endswith("/data/")

    mount_write = await client.put(
        "/api/v1/storage/content",
        headers=headers,
        json={
            "path": "/mount/data/data_list.jsonl",
            "content": '{"id":"sample-1"}\n',
            "content_type": "table/jsonl",
        },
    )
    assert mount_write.status_code == 200, mount_write.text
    mount_raw = await client.get(
        mounts["/mount/"] + "data/data_list.jsonl",
    )
    assert mount_raw.status_code == 200, mount_raw.text
    assert mount_raw.content == b'{"id":"sample-1"}\n'

    draft = {"schema_version": 1, "fields": {"sample-1.label": "pass"}}
    state_response = await client.put(
        f"/api/v1/interactive-artifacts/{artifact_id}/state",
        headers=headers,
        json={"state": draft},
    )
    assert state_response.status_code == 200, state_response.text
    assert state_response.json()["widget_state"] == draft

    result_response = await client.put(
        f"/api/v1/interactive-artifacts/{artifact_id}/result-file",
        headers=headers,
        json={"content": '{"sample-1":{"label":"pass"}}', "content_type": "application/json"},
    )
    assert result_response.status_code == 200, result_response.text
    result = result_response.json()
    assert result["path"] == f"/data/interactive/{artifact_id}/result.json"
    assert result["hash"].startswith("sha256:")
    assert result["revision"] == result["hash"]

    old_session_response = await client.get(
        mounts["/"] + result["path"].lstrip("/"),
    )
    assert old_session_response.status_code == 403
    refreshed_session = (
        await client.post(
            f"/api/v1/interactive-artifacts/{artifact_id}/resource-session",
            headers=headers,
        )
    ).json()
    refreshed_mounts = {
        item["path_prefix"]: item["root_url"]
        for item in refreshed_session["resource_mounts"]
    }
    raw_response = await client.get(
        refreshed_mounts["/"] + result["path"].lstrip("/"),
    )
    assert raw_response.status_code == 200, raw_response.text
    assert raw_response.json() == {"sample-1": {"label": "pass"}}

    custom_result = await client.put(
        f"/api/v1/interactive-artifacts/{artifact_id}/result-file",
        headers=headers,
        json={
            "path": "/data/labels.json",
            "content": '{"sample-1":{"label":"review"}}',
            "content_type": "application/json",
        },
    )
    assert custom_result.status_code == 200, custom_result.text
    assert custom_result.json()["path"] == "/data/labels.json"
    latest_session = (
        await client.post(
            f"/api/v1/interactive-artifacts/{artifact_id}/resource-session",
            headers=headers,
        )
    ).json()
    latest_root = {
        item["path_prefix"]: item["root_url"]
        for item in latest_session["resource_mounts"]
    }["/"]
    custom_raw = await client.get(latest_root + "data/labels.json")
    assert custom_raw.status_code == 200, custom_raw.text
    assert custom_raw.json()["sample-1"]["label"] == "review"
    restored = await client.get(
        f"/api/v1/interactive-artifacts/{artifact_id}",
        headers=headers,
    )
    assert restored.status_code == 200, restored.text
    saved_result = restored.json()["interaction_result_json"]
    assert saved_result["result_path"] == "/data/labels.json"
    assert [item["path"] for item in saved_result["saved_files"]] == [
        f"/data/interactive/{artifact_id}/result.json",
        "/data/labels.json",
    ]
    assert [(item[2], item[3]) for item in mirrored] == [
        (
            f"/data/interactive/{artifact_id}/result.json",
            b'{"sample-1":{"label":"pass"}}',
        ),
        ("/data/labels.json", b'{"sample-1":{"label":"review"}}'),
    ]

    read_only_mount_result = await client.put(
        f"/api/v1/interactive-artifacts/{artifact_id}/result-file",
        headers=headers,
        json={
            "path": "/mount/data/labels.json",
            "content": "{}",
            "content_type": "application/json",
        },
    )
    assert read_only_mount_result.status_code == 400
    assert read_only_mount_result.json()["detail"] == "result_path_must_be_under_data"

    # The same opaque resource endpoint supports media-element byte ranges,
    # which is required for seeking through dataset audio/video previews.
    ranged_response = await client.get(
        latest_root + result["path"].lstrip("/"),
        headers={"Range": "bytes=0-9"},
    )
    assert ranged_response.status_code == 206, ranged_response.text
    assert ranged_response.headers["accept-ranges"] == "bytes"
    assert ranged_response.headers["content-range"].startswith("bytes 0-9/")
    assert ranged_response.content == b'{"sample-1'


@pytest.mark.asyncio
async def test_pre_tool_hitl_persists_fk_safe_and_is_retry_idempotent(
    client, app_engine,
):
    from vibecanvas_api.services.agent_runtime.orchestrator import (
        AgentRuntimeOrchestrator,
    )
    from vibecanvas_api.services.agent_runtime.protocol import (
        RuntimeEvent,
        RuntimeTurnRequest,
    )

    token, me = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}
    boot = await client.get("/api/v1/chats/bootstrap?surface=browser", headers=headers)
    scope_id = boot.json()["carrier_scope_id"]
    chat_id = "c_hitl_fk_order"
    run_id = "t_hitl_fk_order"

    await _seed_chat(me, scope_id=scope_id, chat_id=chat_id, surface="browser")

    await _create_run(
        me,
        chat_id=chat_id,
        run_id=run_id,
        client_request_id="request_hitl",
    )

    turn_request = RuntimeTurnRequest(
        tenant_id=me["tenant_id"],
        user_id=me["user_id"],
        chat_id=chat_id,
        turn_id=run_id,
        runtime_type="langchain",
        runtime_session_id="runtime_hitl_fk_order",
        runtime_root="/runtime/langchain/chats/c_hitl_fk_order",
        message={"role": "user", "content": "Submit the form"},
        approval_mode="agent",
    )
    event = RuntimeEvent(
        event_id="approval_tool_call_submit_1",
        seq=1,
        chat_id=chat_id,
        turn_id=run_id,
        runtime_type="langchain",
        runtime_session_id="runtime_hitl_fk_order",
        type="approval.requested",
        payload={
            "hitl_request_id": "hitl_tool_call_submit_1",
            "title": "Approve browser_click",
            "prompt_text": "Submit the form",
            "actions": [
                {"id": "approve", "label": "Approve"},
                {"id": "deny", "label": "Deny"},
            ],
            "agent_payload": {
                "tool": "browser_click",
                "arguments": {
                    "handle": "h_submit",
                    "purpose": "Submit the form",
                    "require_user_auth": True,
                },
            },
            "runtime_correlation": {
                "source": "langchain",
                "runtime_request_id": "tool_call_submit_1",
                "runtime_method": "tool/approval",
                "runtime_turn_id": run_id,
                "runtime_item_id": "tool_call_submit_1",
            },
        },
    )
    first = await AgentRuntimeOrchestrator._persist_approval_for_turn(
        event, turn_request
    )
    second = await AgentRuntimeOrchestrator._persist_approval_for_turn(
        event, turn_request
    )
    assert first.payload["hitl_request_id"] == second.payload["hitl_request_id"]

    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        counts = (
            await connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM hitl_requests WHERE run_id=:run_id) AS hitl_count, "
                    "(SELECT count(*) FROM interactive_artifacts WHERE run_id=:run_id) AS artifact_count"
                ),
                {"run_id": run_id},
            )
        ).mappings().one()
        linked = (
            await connection.execute(
                text(
                    "SELECT h.hitl_request_id, "
                    "a.hitl_request_id AS artifact_hitl, r.status AS run_status "
                    "FROM hitl_requests h "
                    "JOIN interactive_artifacts a ON a.artifact_id=h.artifact_id "
                    "JOIN agent_runs r ON r.run_id=h.run_id "
                    "WHERE h.run_id=:run_id"
                ),
                {"run_id": run_id},
            )
        ).mappings().one()
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        persisted_request = await HitlRepo(session).get_request(
            linked["hitl_request_id"]
        )
        assert persisted_request is not None
    assert counts["hitl_count"] == 1
    assert counts["artifact_count"] == 1
    assert linked["artifact_hitl"] == linked["hitl_request_id"]
    assert linked["run_status"] == "waiting_approval"
    assert persisted_request.runtime_correlation_json == {
        "source": "langchain",
        "runtime_request_id": "tool_call_submit_1",
        "runtime_method": "tool/approval",
        "runtime_thread_id": None,
        "runtime_turn_id": run_id,
        "runtime_item_id": "tool_call_submit_1",
        "runtime_approval_id": None,
    }


@pytest.mark.asyncio
async def test_same_turn_runtime_input_is_encrypted_durable_and_resumable(
    client, app_engine,
):
    from vibecanvas_api.services.agent_runtime.orchestrator import (
        AgentRuntimeOrchestrator,
    )
    from vibecanvas_api.services.agent_runtime.protocol import (
        RuntimeEvent,
        RuntimeTurnRequest,
    )

    token, me = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}
    boot = await client.get("/api/v1/chats/bootstrap?surface=chat", headers=headers)
    scope_id = boot.json()["carrier_scope_id"]
    chat_id = "c_native_input"
    run_id = "t_native_input"
    hitl_request_id = "hitl_native_input"
    artifact_id = "ia_native_input"
    secret_marker = "PRIVATE_NATIVE_INPUT_73f1"

    await _seed_chat(me, scope_id=scope_id, chat_id=chat_id, surface="chat")
    await _create_run(
        me,
        chat_id=chat_id,
        run_id=run_id,
        client_request_id="request_native_input",
    )
    turn_request = RuntimeTurnRequest(
        tenant_id=me["tenant_id"],
        user_id=me["user_id"],
        chat_id=chat_id,
        turn_id=run_id,
        runtime_type="codex",
        runtime_session_id="runtime_native_input",
        runtime_root="/runtime/.codex",
        message={"role": "user", "content": "ask for input"},
        model={
            "id": "gpt-codex-current",
            "base_url": "http://platform.test/api/internal/runtime-model/v1",
            "api_key": "turn-capability",
        },
    )
    definition = {
        "kind": "interactive_artifact",
        "schema_version": 1,
        "artifact_id": artifact_id,
        "hitl_request_id": hitl_request_id,
        "title": "Input required",
        "component_type": "user_input",
        "props": {
            "message": "Provide a value",
            "questions": [{
                "id": "token",
                "label": secret_marker,
                "secret": True,
                "multiple": False,
                "options": [],
            }],
        },
        "interaction_schema": {
            "interaction_type": "input",
            "submit_label": "Submit",
            "cancel_label": "Cancel",
            "hide_result": True,
        },
        "completion_mode": "wait_for_submit",
        "widget_state": {},
        "interaction_state": {
            "is_interacted": False,
            "status": "pending",
            "result": {},
        },
    }
    event = RuntimeEvent(
        event_id="native_input_required",
        seq=1,
        chat_id=chat_id,
        turn_id=run_id,
        runtime_type="codex",
        runtime_session_id="runtime_native_input",
        type="interaction.required",
        payload={
            "hitl_request_id": hitl_request_id,
            "artifact_id": artifact_id,
            "tool_call_id": "input-item-1",
            "title": "Input required",
            "prompt_text": "Provide a value",
            "resume_mode": "same_turn",
            "interaction_definition": definition,
            "agent_payload": {
                "method": "item/tool/requestUserInput",
                "awaiting_user_input": True,
            },
            "runtime_correlation": {
                "source": "codex_app_server",
                "runtime_request_id": 73,
                "runtime_method": "item/tool/requestUserInput",
                "runtime_thread_id": "codex-thread",
                "runtime_turn_id": "codex-turn",
                "runtime_item_id": "input-item-1",
            },
        },
    )

    persisted = await AgentRuntimeOrchestrator._persist_interaction_for_turn(
        event, turn_request
    )
    assert persisted.payload["resume_mode"] == "same_turn"
    projection = persisted.payload["projection_event"]
    assert projection["type"] == "tool_update"
    assert projection["artifact"]["payload"]["artifact"] == definition

    async with session_scope(tenant_id=me["tenant_id"]) as session:
        request = await HitlRepo(session).get_request(hitl_request_id)
        assert request is not None
        assert request.status == "pending"
        assert request.runtime_correlation_json["runtime_request_id"] == 73
    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        stored = (
            await connection.execute(
                text(
                    "SELECT h.private_ciphertext AS hitl_ciphertext, "
                    "a.private_ciphertext AS artifact_ciphertext, "
                    "r.status AS run_status "
                    "FROM hitl_requests h JOIN interactive_artifacts a "
                    "ON a.artifact_id=h.artifact_id "
                    "JOIN agent_runs r ON r.run_id=h.run_id "
                    "WHERE h.hitl_request_id=:hitl_request_id"
                ),
                {"hitl_request_id": hitl_request_id},
            )
        ).mappings().one()
    assert secret_marker not in stored["hitl_ciphertext"]
    assert secret_marker not in stored["artifact_ciphertext"]
    assert stored["run_status"] == "waiting_approval"

    decision = await client.post(
        f"/api/v1/hitl-requests/{hitl_request_id}/decision",
        headers=headers,
        json={
            "decision": "submit",
            "decision_payload": {
                "artifact_id": artifact_id,
                "widget_state": {"token": "secret-value"},
            },
            "interaction_result": {
                "artifact_id": artifact_id,
                "widget_state": {"token": "secret-value"},
            },
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "submitted"
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        submitted = await HitlRepo(session).get_request(hitl_request_id)
        assert submitted is not None
        assert submitted.interaction_result_json["widget_state"] == {
            "token": "secret-value"
        }


@pytest.mark.asyncio
async def test_concurrent_hitl_decisions_freeze_once_without_stealing_turn_sequence(
    client, app_engine,
):
    from vibecanvas_api.services.agent_runtime.orchestrator import (
        AgentRuntimeOrchestrator,
    )
    from vibecanvas_api.services.agent_runtime.protocol import (
        RuntimeEvent,
        RuntimeTurnRequest,
    )
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.hitl_repo import HitlRepo
    from vibecanvas_api.services.agent_run_writer import AgentRunWriter

    token, me = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}
    boot = await client.get("/api/v1/chats/bootstrap?surface=browser", headers=headers)
    scope_id = boot.json()["carrier_scope_id"]
    chat_id = "c_hitl_concurrent"
    run_id = "t_hitl_concurrent"

    await _seed_chat(me, scope_id=scope_id, chat_id=chat_id, surface="browser")

    await _create_run(
        me,
        chat_id=chat_id,
        run_id=run_id,
        client_request_id="request_hitl_concurrent",
    )

    writer = AgentRunWriter(run_id=run_id, tenant_id=me["tenant_id"])
    await writer.emit(1, "started", {"turn_id": run_id})

    turn_request = RuntimeTurnRequest(
        tenant_id=me["tenant_id"],
        user_id=me["user_id"],
        chat_id=chat_id,
        turn_id=run_id,
        runtime_type="langchain",
        runtime_session_id="runtime_hitl_concurrent",
        runtime_root="/runtime/langchain/chats/c_hitl_concurrent",
        message={"role": "user", "content": "Click the control"},
    )
    hitl_request_id = "hitl_tool_call_concurrent"
    await AgentRuntimeOrchestrator._persist_approval_for_turn(
        RuntimeEvent(
            event_id="approval_tool_call_concurrent",
            seq=1,
            chat_id=chat_id,
            turn_id=run_id,
            runtime_type="langchain",
            runtime_session_id="runtime_hitl_concurrent",
            type="approval.requested",
            payload={
                "hitl_request_id": hitl_request_id,
                "title": "Approve browser_click",
                "prompt_text": "Click the control",
                "agent_payload": {
                    "tool": "browser_click",
                    "arguments": {"handle": "h1", "require_user_auth": True},
                },
                "runtime_correlation": {
                    "source": "langchain",
                    "runtime_request_id": "tool_call_concurrent",
                    "runtime_method": "tool/approval",
                    "runtime_turn_id": run_id,
                    "runtime_item_id": "tool_call_concurrent",
                },
            },
        ),
        turn_request,
    )

    async def decide(decision: str):
        async with session_scope(tenant_id=me["tenant_id"]) as session:
            return await HitlRepo(session).resolve(
                hitl_request_id=hitl_request_id,
                decision=decision,
                decision_payload={"source": decision},
            )

    (approved, approved_applied), (denied, denied_applied) = await asyncio.gather(
        decide("approve"), decide("deny")
    )
    assert approved is not None and denied is not None
    assert [approved_applied, denied_applied].count(True) == 1
    assert approved.status == denied.status
    assert approved.status in {"approved", "denied"}

    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        out_of_band_event_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM agent_run_events "
                    "WHERE run_id=:run_id AND event_type='HITL_RESOLVED'"
                ),
                {"run_id": run_id},
            )
        ).scalar_one()
    assert out_of_band_event_count == 0

    # The outer Agent Loop projects the durable decision through the normal
    # writer. It can safely use the next SSE/buffer sequence without colliding
    # with an HTTP-side last_event_id + 1 allocation.
    await writer.emit(2, "HITL_RESOLVED", {
        "hitl_request_id": hitl_request_id,
        "status": approved.status,
    })
    await writer.emit(3, "done", {})

    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        rows = (
            await connection.execute(
                text(
                    "SELECT seq, event_type FROM agent_run_events "
                    "WHERE run_id=:run_id ORDER BY seq"
                ),
                {"run_id": run_id},
            )
        ).all()
    assert rows == [(1, "started"), (2, "HITL_RESOLVED"), (3, "done")]


@pytest.mark.asyncio
async def test_render_interactive_wait_persists_under_always_allow_and_restores_frozen_state(
    client, app_engine,
):
    from langchain_core.messages import ToolMessage
    from vibecanvas_api.agent import (
        AgentContext,
        _ensure_post_tool_interaction_request,
    )
    from vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive import (
        _persist_interactive_state,
    )

    token, me = await _register(client)
    headers = {"Authorization": f"Bearer {token}"}
    boot = await client.get("/api/v1/chats/bootstrap?surface=chat", headers=headers)
    scope_id = boot.json()["carrier_scope_id"]
    chat_id = "c_interactive_restore"
    run_id = "t_interactive_restore"
    artifact_id = "ia_interactive_restore"
    private_marker = "SENSITIVE_INTERACTIVE_MARKER_5c2e"

    await _seed_chat(me, scope_id=scope_id, chat_id=chat_id, surface="chat")

    await _create_run(
        me,
        chat_id=chat_id,
        run_id=run_id,
        client_request_id="request_interactive",
    )

    definition = {
        "kind": "interactive_artifact",
        "artifact_id": artifact_id,
        "title": f"Dataset review {private_marker}",
        "component_type": "html_preview",
        "props": {"html": "<form><input name='label'><button type='submit'>Submit</button></form>"},
        "interaction_schema": {
            "interaction_type": "continue",
            "submit_label": "Continue",
        },
        "completion_mode": "wait_for_submit",
        "require_human_confirm": True,
        "widget_state": {},
        "hitl_request_id": None,
    }
    agent_context = AgentContext(
        tenant_id=me["tenant_id"],
        username=me["user_id"],
        chat_id=chat_id,
        turn_id=run_id,
        approval_mode="always_allow",
    )
    runtime = SimpleNamespace(
        context=agent_context,
    )
    await _persist_interactive_state(
        runtime=runtime,
        artifact_id=artifact_id,
        definition=definition,
        component_type="html_preview",
        completion_mode="wait_for_submit",
        title=f"Dataset review {private_marker}",
        path=None,
        content_hash="sha256:test",
    )

    # The tool owns artifact content only. No HITL control record exists until
    # the outer Agent Loop observes the waiting ToolMessage.
    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        hitl_before = (
            await connection.execute(
                text("SELECT count(*) FROM hitl_requests WHERE artifact_id=:artifact_id"),
                {"artifact_id": artifact_id},
            )
        ).scalar_one()
    assert hitl_before == 0

    checkpoint_updates = []

    class FakeAgent:
        def update_state(self, _config, update):
            checkpoint_updates.append(update)

    message = ToolMessage(
        content=json.dumps({
            "status": "success",
            "output": {
                "artifact_id": artifact_id,
                "completion_mode": "wait_for_submit",
            },
        }),
        name="render_interactive",
        tool_call_id="tc_interactive_restore",
        artifact={
            "status": "success",
            "payload": {"artifact": definition, "hash": "sha256:test"},
            "meta": {"tool": "render_interactive"},
        },
    )
    published_interactions = []

    async def publish_interaction(payload):
        published_interactions.append(payload)

    hitl_request_id = await _ensure_post_tool_interaction_request(
        agent=FakeAgent(),
        config={"configurable": {"thread_id": "thread_interactive"}},
        context=agent_context,
        msg=message,
        publish_interaction=publish_interaction,
    )
    assert checkpoint_updates[-1]["interactive_artifact_refs"][artifact_id] == {
        "artifact_id": artifact_id,
        "hitl_request_id": hitl_request_id,
        "status": "pending",
        "content_hash": "sha256:test",
        "db_ref": f"interactive_artifact:{artifact_id}",
    }
    assert message.artifact["payload"]["artifact"]["hitl_request_id"] == hitl_request_id

    # The sandbox publishes only a Runtime fact. The host validates the
    # durable artifact and owns the encrypted product-state transaction.
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        assert await HitlRepo(session).get_request(hitl_request_id) is None
    from vibecanvas_api.services.agent_runtime.orchestrator import (
        AgentRuntimeOrchestrator,
    )
    from vibecanvas_api.services.agent_runtime.protocol import (
        RuntimeEvent,
        RuntimeType,
    )

    await AgentRuntimeOrchestrator._persist_interaction_for_turn(
        RuntimeEvent(
            event_id="rte_interactive_restore",
            seq=1,
            chat_id=chat_id,
            turn_id=run_id,
            runtime_type=RuntimeType.LANGCHAIN,
            runtime_session_id="rt_interactive_restore",
            type="interaction.required",
            payload=published_interactions[0],
        ),
        SimpleNamespace(
            tenant_id=me["tenant_id"],
            chat_id=chat_id,
            turn_id=run_id,
        ),
    )

    async with session_scope(tenant_id=me["tenant_id"]) as session:
        persisted_request = await HitlRepo(session).get_request(hitl_request_id)
        assert persisted_request is not None
        hitl_type = persisted_request.hitl_type
        runtime_correlation = persisted_request.runtime_correlation_json
        ui_payload = persisted_request.ui_payload_json
    assert hitl_type == "post_tool_review"
    assert runtime_correlation == {
        "source": "langchain",
        "runtime_request_id": artifact_id,
        "runtime_method": "tool/postInteraction",
        "runtime_thread_id": None,
        "runtime_turn_id": run_id,
        "runtime_item_id": "tc_interactive_restore",
        "runtime_approval_id": None,
    }
    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        stored = (
            await connection.execute(
                text(
                    "SELECT h.private_ciphertext AS hitl_ciphertext, "
                    "a.private_ciphertext AS artifact_ciphertext "
                    "FROM hitl_requests h JOIN interactive_artifacts a "
                    "ON a.artifact_id=h.artifact_id "
                    "WHERE h.hitl_request_id=:hitl_request_id"
                ),
                {"hitl_request_id": hitl_request_id},
            )
        ).mappings().one()
        old_columns = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND (("
                    "table_name='hitl_requests' AND column_name IN ("
                    "'title','prompt_text','ui_payload_json','agent_payload_json',"
                    "'decision_payload_json','runtime_correlation_json',"
                    "'resume_payload_json','interaction_result_json')) OR ("
                    "table_name='interactive_artifacts' AND column_name IN ("
                    "'title','definition_json','widget_state_json',"
                    "'interaction_result_json','artifact_ref')))"
                )
            )
        ).all()
    assert private_marker not in stored["hitl_ciphertext"]
    assert private_marker not in stored["artifact_ciphertext"]
    assert old_columns == []
    projection = ui_payload["projection_event"]
    assert projection["type"] == "tool_end"
    assert projection["tool_call_id"] == "tc_interactive_restore"
    assert projection["status"] == "done"
    assert projection["artifact"]["payload"]["hitl_request_id"] == hitl_request_id
    assert (
        projection["artifact"]["payload"]["artifact"]["hitl_request_id"]
        == hitl_request_id
    )

    decision = await client.post(
        f"/api/v1/hitl-requests/{hitl_request_id}/decision",
        headers=headers,
        json={
            "decision": "submit",
            "decision_payload": {
                "artifact_id": artifact_id,
                "widget_state": {"value": 7},
            },
            "interaction_result": {
                "artifact_id": artifact_id,
                "widget_state": {"value": 7},
            },
        },
    )
    assert decision.status_code == 200, decision.text
    restored = await client.get(
        f"/api/v1/interactive-artifacts/{artifact_id}", headers=headers,
    )
    assert restored.status_code == 200, restored.text
    artifact = restored.json()["artifact"]
    assert artifact["hitl_request_id"] == hitl_request_id
    assert artifact["widget_state"] == {"value": 7}
    assert artifact["interaction_state"]["is_interacted"] is True
    assert artifact["interaction_state"]["status"] == "submitted"

    async with session_scope(tenant_id=me["tenant_id"]) as session:
        refs = await HitlRepo(session).project_artifact_refs_for_chat(chat_id)
    assert refs[artifact_id] == {
        "artifact_id": artifact_id,
        "hitl_request_id": hitl_request_id,
        "status": "submitted",
        "content_hash": "sha256:test",
        "db_ref": f"interactive_artifact:{artifact_id}",
        "widget_state": {"value": 7},
    }

    async with app_engine.connect() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,false)"),
            {"tenant": me["tenant_id"]},
        )
        run_status = (
            await connection.execute(
                text("SELECT status FROM agent_runs WHERE run_id=:run_id"),
                {"run_id": run_id},
            )
        ).scalar_one()
    # render_interactive records a durable post-tool interaction, but V1 does
    # not suspend/resume the current graph stack for it.
    assert run_status == "running"


@pytest.mark.asyncio
async def test_runtime_adapter_reconciles_interaction_ref_without_rewriting_messages():
    from vibecanvas_api.agent import _reconcile_interactive_artifact_refs_before_model

    class FakeAgent:
        def __init__(self):
            self.update = None

        async def aupdate_state(self, _config, update):
            self.update = update

        def update_state(self, _config, _update):
            raise AssertionError("async Runtime state must not use sync checkpointer API")

    agent = FakeAgent()
    merged = await _reconcile_interactive_artifact_refs_before_model(
        agent,
        {"configurable": {"thread_id": "thread_1"}},
        checkpoint_refs={
            "ia_old": {"artifact_id": "ia_old", "status": "rendered"},
        },
        durable_refs={
            "ia_new": {
                "artifact_id": "ia_new",
                "hitl_request_id": "hitl_new",
                "status": "submitted",
                "content_hash": "sha256:new",
                "db_ref": "interactive_artifact:ia_new",
                "widget_state": {"value": 7},
            },
        },
    )

    assert agent.update == {"interactive_artifact_refs": merged}
    assert merged == {
            "ia_old": {"artifact_id": "ia_old", "status": "rendered"},
            "ia_new": {
                "artifact_id": "ia_new",
                "hitl_request_id": "hitl_new",
                "status": "submitted",
                "content_hash": "sha256:new",
                "db_ref": "interactive_artifact:ia_new",
                "widget_state": {"value": 7},
            },
    }


@pytest.mark.asyncio
async def test_next_turn_reconciles_durable_interaction_before_model_input():
    from vibecanvas_api.agent import _reconcile_interactive_artifact_refs_before_model

    calls = []

    class FakeAgent:
        def update_state(self, config, update):
            calls.append((config, update))

    config = {"configurable": {"thread_id": "thread_1"}}
    old = {"ia_old": {"artifact_id": "ia_old", "status": "rendered"}}
    durable = {
        "ia_old": {
            "artifact_id": "ia_old",
            "status": "submitted",
            "widget_state": {"value": 7},
            "db_ref": "interactive_artifact:ia_old",
        }
    }

    merged = await _reconcile_interactive_artifact_refs_before_model(
        FakeAgent(),
        config,
        checkpoint_refs=old,
        durable_refs=durable,
    )

    assert merged == durable
    assert calls == [(config, {"interactive_artifact_refs": durable})]

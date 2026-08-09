"""Application-layer checkpoint access gate.

The LangGraph checkpoint tables (checkpoints / checkpoint_writes /
checkpoint_blobs) carry no tenant_id and cannot have Postgres RLS, so
conversation history is NOT RLS-isolated at the DB layer. The defense:
GET .../chats/{chat_id}/messages must resolve chat_id through the
RLS-protected `chats` table FIRST — a chat owned by another tenant is
invisible there → 404, and the checkpoint read is never reached.
"""

import pytest

from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope


@pytest.mark.asyncio
async def test_chat_history_blocked_cross_tenant(client, app_engine, monkeypatch):
    # `ctx.checkpointer` is a process-global set by the app lifespan; an
    # earlier lifespan-running test leaves it pointing at a checkpointer
    # whose connection pool is already closed. This test only verifies the
    # §5.4 ownership gate (A → 200, B → 404), which fires BEFORE the
    # checkpoint read — so pin the global to None: get_chat_history then
    # short-circuits to an empty 200 for the owner, deterministically.
    # TODO(T10): conftest should reset the ctx.* module globals between
    # tests so a previous lifespan cannot leak its closed checkpointer.
    monkeypatch.setattr("vibecanvas_api.context.checkpointer", None)

    # tenant A registers + creates a workflow
    a_hdr = {"Authorization": "Bearer " + (await client.post(
        "/api/v1/auth/register",
        json={"email": "ca@example.com", "username": "Test User", "password": "pw12345678"})
        ).json()["session_token"]}
    a_me = (await client.get("/api/v1/auth/me", headers=a_hdr)).json()
    wf = (await client.post("/api/v1/workflows",
          json={"name": "A wf", "description": "", "tags": []},
          headers=a_hdr)).json()
    wf_id = wf["wf_id"]

    # seed a chats row owned by tenant A — the chats.tenant_id column
    # default current_setting('app.tenant_id') fills the tenant from the
    # set_config'd GUC.
    async with session_scope(tenant_id=a_me["tenant_id"]) as session:
        await ChatRepo(session, a_me["user_id"]).register_session(
            wf_id,
            name="A chat",
            chat_id="c1",
        )

    # tenant A reads its own chat history → 200 (chat is visible via RLS)
    r_a = await client.get(
        f"/api/v1/chat-scopes/{wf_id}/chats/c1/messages", headers=a_hdr)
    assert r_a.status_code == 200

    # tenant B requesting A's chat_id → 404: the chats lookup is
    # RLS-blocked, so the gate fires before the checkpoint read.
    b_tok = (await client.post("/api/v1/auth/register",
             json={"email": "cb@example.com", "username": "Test User", "password": "pw12345678"})
             ).json()["session_token"]
    r_b = await client.get(
        f"/api/v1/chat-scopes/{wf_id}/chats/c1/messages",
        headers={"Authorization": f"Bearer {b_tok}"})
    assert r_b.status_code == 404

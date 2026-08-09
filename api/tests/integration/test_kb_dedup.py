"""KB / RAG T12 — content-hash dedup integration.

Upload the same blob twice to the same KB → second upload must 409
with ``kb_duplicate_content_hash`` (partial UNIQUE on
``(kb_id, content_hash) WHERE deleted_at IS NULL`` from migration 007).

This complements ``test_kb_routes.py::test_upload_file_dedup_409``
(which patches the broker call out) by also wiring through the eager
celery_app.send_task replacement — confirms the dedup IntegrityError
happens BEFORE we ever try to enqueue a duplicate index task.
"""
from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.routes.kb import (
    KbCreate,
    create_kb,
    upload_file,
)
from vibecanvas_api.storage.db import session_scope


# --------------------------------------------------------------------- seed


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tenant_id},
        )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": user_id, "t": tenant_id,
             "e": f"kb-dedup-{uuid.uuid4().hex[:6]}@example.com"},
        )


class _StubCtx:
    def __init__(self, tenant_id, user_id):
        self.tenant_id = str(tenant_id)
        self.user_id = str(user_id)
        self.active_organization_id = str(tenant_id)
        self.session_id = "test-session"
        self.session_generation = 1
        self.membership_id = "test-membership"
        self.membership_role = "owner"
        self.membership_status = "active"
        self.authentication_strength = "password"
        self.email = "stub@example.com"


class _StubRequest:
    def __init__(self):
        self.headers = {}
        self.client = None
        self.state = SimpleNamespace(request_id="kb-dedup")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")


def _make_upload(name: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        filename=name,
        file=io.BytesIO(content),
        headers={"content-type": content_type},
    )


# --------------------------------------------------------------------- test


@pytest.mark.asyncio
async def test_same_content_hash_twice_409(pg_engine):
    """Two uploads with the same SHA-256 → second one is 409 with the
    existing file name surfaced in the body."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        kb = await create_kb(
            body=KbCreate(name="K"), request=_StubRequest(),
            ctx=ctx, session=s, service=_AllowAuthz(),
        )
        await s.commit()
        kb_id = uuid.UUID(kb.id)

    blob = b"identical content hashed once"
    sent_tasks: list = []

    def _capture(name, *args, **kwargs):
        sent_tasks.append({"name": name, "kwargs": kwargs})

    with patch(
        "vibecanvas_api.routes.kb.celery_app.send_task",
        side_effect=_capture,
    ):
        # First — should succeed.
        async with session_scope(tenant_id=str(tenant_id)) as s:
            r1 = await upload_file(
                kb_id=kb_id,
                request=_StubRequest(),
                file=_make_upload("dup.txt", blob, "text/plain"),
                ctx=ctx, session=s, service=_AllowAuthz(),
            )
        assert r1["status"] == "pending"

        # Second — same blob → 409.
        async with session_scope(tenant_id=str(tenant_id)) as s:
            with pytest.raises(HTTPException) as exc_info:
                await upload_file(
                    kb_id=kb_id,
                    request=_StubRequest(),
                    file=_make_upload("dup.txt", blob, "text/plain"),
                    ctx=ctx, session=s, service=_AllowAuthz(),
                )
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "kb_duplicate_content_hash"
    assert detail["existing_file_name"] == "dup.txt"
    # Only the FIRST upload enqueued a task; the second short-circuited
    # before reaching ``send_task``.
    assert len(sent_tasks) == 1, (
        f"expected exactly one task enqueued (the first upload), "
        f"got {len(sent_tasks)}: {sent_tasks}"
    )

"""KB/RAG T6 — ``routes/kb.py`` smoke tests.

Strategy: handler-direct-call (mirrors ``test_mcp_servers_create.py`` +
``test_kb_indexer.py``). The plan's ``client`` + ``auth_headers``
fixtures do not exist in this repo's conftest; this codebase consistently
calls the route handler functions directly with a stub ``AuthContext``
and a manually-opened ``session_scope(tenant_id=...)``. That tests the
same body validation + DB ordering + dedup + size + parser-type paths
without bringing up the auth stack.

Coverage (4 cases — match plan §6.4):

1. ``test_create_and_list_kb`` — POST /kb + GET /kb round-trip writes
   and reads the same row.
2. ``test_upload_file_dedup_409`` — second upload of the same content
   hash to the same KB raises 409 ``kb_duplicate_content_hash``.
3. ``test_upload_too_large_413`` — payload > 50 MB raises 413
   ``kb_file_too_large``.
4. ``test_unsupported_type_400`` — filename + MIME mismatch (or absent
   in registry) raises 400 ``kb_unsupported_file_type``.

Cases 2-4 also patch ``celery_app.send_task`` and ``get_object_store``
so the test doesn't actually hit the broker / boto3 client.
"""
from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import text

from vibecanvas_api.authorization.types import Action, Decision
from vibecanvas_api.routes.kb import (
    KbCreate,
    create_kb,
    list_kbs,
    upload_file,
)
from vibecanvas_api.storage.db import session_scope


# --------------------------------------------------------------------- seed


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
    """Insert tenant + user via the RLS-bypassing superuser engine. Auth
    tables are RLS-free so a plain ``begin()`` block is fine. Matches the
    seeding pattern from ``test_kb_indexer.py`` / ``test_repo_kb.py``."""
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
             "e": f"kb-routes-{uuid.uuid4().hex[:6]}@example.com"},
        )


class _StubCtx:
    """Lightweight stand-in for ``AuthContext``. The KB handlers only
    read ``tenant_id`` / ``user_id`` (both strings); ``email`` is unused."""

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
        self.state = SimpleNamespace(request_id="kb-route-test")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    def __init__(self, resource_ids=()):
        self._resource_ids = tuple(str(value) for value in resource_ids)

    async def check(self, *args, **kwargs):
        return Decision(
            allowed=True,
            capabilities=frozenset(Action),
            effective_role="manager",
            reason_code="test_fixture",
        )

    async def list_authorized_ids(self, *args, **kwargs):
        return self._resource_ids

    async def batch_check(self, checks):
        return tuple(
            Decision(
                allowed=True,
                capabilities=frozenset(Action),
                effective_role="manager",
                reason_code="test_fixture",
            )
            for _ in checks
        )


def _make_upload(name: str, content: bytes, content_type: str) -> UploadFile:
    """Build a starlette UploadFile from in-memory bytes — matches the
    shape FastAPI hands to ``upload_file`` after multipart parsing."""
    return UploadFile(
        filename=name,
        file=io.BytesIO(content),
        headers={"content-type": content_type},
    )


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_create_and_list_kb(pg_engine):
    """POST /kb followed by GET /kb returns the just-created row.

    Exercises the create_kb -> list_kbs roundtrip on a real DB so the
    UNIQUE-name partial index, the soft-delete filter, and the RLS
    binding all participate.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        created = await create_kb(
            body=KbCreate(name="HR", description="x"),
            request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.commit()

    assert created.name == "HR"
    assert created.description == "x"
    kb_id = created.id

    async with session_scope(tenant_id=str(tenant_id)) as s:
        rows = await list_kbs(
            request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz((kb_id,)),
        )

    assert any(k.id == kb_id for k in rows), (
        f"Expected KB {kb_id} in list, got {[k.id for k in rows]}"
    )


@pytest.mark.asyncio
async def test_upload_file_dedup_409(pg_engine):
    """Two uploads with the same content_hash → 409.

    First upload succeeds (creates the row, writes to in-memory object
    store, enqueues celery). Second hits the partial UNIQUE on
    ``(kb_id, content_hash) WHERE deleted_at IS NULL`` → IntegrityError
    → 409 ``kb_duplicate_content_hash`` with ``existing_file_name``
    in the body.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        kb = await create_kb(
            body=KbCreate(name="X"), request=_StubRequest(),
            ctx=ctx, session=s, service=_AllowAuthz(),
        )
        await s.commit()

    blob = b"hello world content here"
    sent: list[dict] = []
    with patch(
        "vibecanvas_api.routes.kb.celery_app.send_task",
        side_effect=lambda *a, **kw: sent.append({"args": a, "kwargs": kw}),
    ):
        # First upload — succeeds, returns pending.
        async with session_scope(tenant_id=str(tenant_id)) as s:
            up1 = _make_upload("a.txt", blob, "text/plain")
            r1 = await upload_file(
                kb_id=uuid.UUID(kb.id), request=_StubRequest(), file=up1,
                ctx=ctx, session=s, service=_AllowAuthz(),
            )
        assert r1["status"] == "pending"
        assert "file_id" in r1

        # Second upload — same blob → 409 dedup.
        async with session_scope(tenant_id=str(tenant_id)) as s:
            up2 = _make_upload("a.txt", blob, "text/plain")
            with pytest.raises(HTTPException) as exc_info:
                await upload_file(
                    kb_id=uuid.UUID(kb.id), request=_StubRequest(), file=up2,
                    ctx=ctx, session=s, service=_AllowAuthz(),
                )

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict), f"expected dict body, got {detail!r}"
    assert detail.get("error") == "kb_duplicate_content_hash"
    assert detail.get("existing_file_name") == "a.txt"


@pytest.mark.asyncio
async def test_upload_too_large_413(pg_engine):
    """A 51 MB payload → 413 ``kb_file_too_large`` BEFORE any DB write
    or object-store write happens. Step 1 of the upload pipeline."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        kb = await create_kb(
            body=KbCreate(name="X"), request=_StubRequest(),
            ctx=ctx, session=s, service=_AllowAuthz(),
        )
        await s.commit()

    blob = b"x" * (51 * 1024 * 1024)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        up = _make_upload("big.txt", blob, "text/plain")
        with pytest.raises(HTTPException) as exc_info:
            await upload_file(
                kb_id=uuid.UUID(kb.id), request=_StubRequest(), file=up,
                ctx=ctx, session=s, service=_AllowAuthz(),
            )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "kb_file_too_large"


@pytest.mark.asyncio
async def test_unsupported_type_400(pg_engine):
    """A binary file (``application/octet-stream`` MIME, ``.bin``
    extension) is not in the parser registry → 400
    ``kb_unsupported_file_type``."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        kb = await create_kb(
            body=KbCreate(name="X"), request=_StubRequest(),
            ctx=ctx, session=s, service=_AllowAuthz(),
        )
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        up = _make_upload("a.bin", b"x", "application/octet-stream")
        with pytest.raises(HTTPException) as exc_info:
            await upload_file(
                kb_id=uuid.UUID(kb.id), request=_StubRequest(), file=up,
                ctx=ctx, session=s, service=_AllowAuthz(),
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "kb_unsupported_file_type"


# --------------------------------------------------------------- router mount


def test_router_mounted_under_api_v1_kb():
    """The kb router is registered on ``build_app()`` so the upload /
    search / CRUD endpoints are reachable in production."""
    from vibecanvas_api.app import build_app
    from vibecanvas_api.authorization.manifest import application_route_contexts

    app = build_app()
    paths = [r.path for r in application_route_contexts(app)]
    assert "/api/v1/kb" in paths
    assert "/api/v1/kb/{kb_id}" in paths
    assert "/api/v1/kb/{kb_id}/files" in paths
    assert "/api/v1/kb/{kb_id}/files/{file_id}/content" in paths
    assert "/api/v1/kb/search" in paths

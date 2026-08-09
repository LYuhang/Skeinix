from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.security import purge
def test_every_purge_phase_has_a_real_handler():
    assert set(purge._PHASE_HANDLERS) == set(purge.PHASES)
    assert all(callable(purge._PHASE_HANDLERS[name]) for name in purge.PHASES)


@pytest.mark.asyncio
async def test_purge_job_completes_only_after_all_phases(pg_engine, monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tenants (tenant_id, name) VALUES (:t, 'purge')"
        ), {"t": tenant_id})
        await conn.execute(text(
            "INSERT INTO users (user_id, tenant_id, email, display_name, status) "
            "VALUES (:u, :t, :e, '', 'pending_deletion')"
        ), {"u": user_id, "t": tenant_id, "e": f"{user_id}@x.test"})
        await conn.execute(text(
            "INSERT INTO account_deletion_requests "
            "(id, user_id, tenant_id, email_snapshot, status, purge_after) "
            "VALUES (:r, :u, :t, 'x@x.test', 'pending', now())"
        ), {"r": request_id, "u": user_id, "t": tenant_id})
        await conn.execute(text(
            "INSERT INTO data_purge_jobs "
            "(job_id, deletion_request_id, user_id, tenant_id, status, available_at) "
            "VALUES (:j, :r, :u, :t, 'queued', now())"
        ), {"j": job_id, "r": request_id, "u": user_id, "t": tenant_id})

    from vibecanvas_api.storage import db as db_mod
    old = db_mod._admin_engine
    db_mod._admin_engine = pg_engine
    called: list[str] = []

    def handler(name):
        async def _run(_lease):
            called.append(name)
        return _run

    monkeypatch.setattr(
        purge,
        "_PHASE_HANDLERS",
        {name: handler(name) for name in purge.PHASES},
    )
    try:
        lease = await purge.claim_due_purge_job()
        assert lease is not None
        await purge.run_purge_job(lease)
    finally:
        db_mod._admin_engine = old

    async with pg_engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, completed_phases FROM data_purge_jobs WHERE job_id = :j"
        ), {"j": job_id})).one()
        request_status = (await conn.execute(text(
            "SELECT status FROM account_deletion_requests WHERE id = :r"
        ), {"r": request_id})).scalar_one()
    assert called == list(purge.PHASES)
    assert row.status == "completed"
    assert row.completed_phases == list(purge.PHASES)
    assert request_status == "purged"


@pytest.mark.asyncio
async def test_failed_purge_is_not_automatically_reclaimed(pg_engine, monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tenants (tenant_id, name) VALUES (:t, 'purge-fail')"
        ), {"t": tenant_id})
        await conn.execute(text(
            "INSERT INTO users (user_id, tenant_id, email, display_name, status) "
            "VALUES (:u, :t, :e, '', 'pending_deletion')"
        ), {"u": user_id, "t": tenant_id, "e": f"{user_id}@x.test"})
        await conn.execute(text(
            "INSERT INTO account_deletion_requests "
            "(id, user_id, tenant_id, email_snapshot, status, purge_after) "
            "VALUES (:r, :u, :t, 'f@x.test', 'pending', now())"
        ), {"r": request_id, "u": user_id, "t": tenant_id})
        await conn.execute(text(
            "INSERT INTO data_purge_jobs "
            "(job_id, deletion_request_id, user_id, tenant_id, status, available_at) "
            "VALUES (:j, :r, :u, :t, 'queued', now())"
        ), {"j": job_id, "r": request_id, "u": user_id, "t": tenant_id})

    from vibecanvas_api.storage import db as db_mod
    old = db_mod._admin_engine
    db_mod._admin_engine = pg_engine

    async def fail(_lease):
        raise RuntimeError("password=must-not-persist")

    monkeypatch.setattr(
        purge,
        "_PHASE_HANDLERS",
        {**purge._PHASE_HANDLERS, purge.PHASES[0]: fail},
    )
    try:
        lease = await purge.claim_due_purge_job()
        assert lease is not None
        with pytest.raises(RuntimeError):
            await purge.run_purge_job(lease)
        assert await purge.claim_due_purge_job() is None
    finally:
        db_mod._admin_engine = old

    async with pg_engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, last_error_message FROM data_purge_jobs WHERE job_id = :j"
        ), {"j": job_id})).one()
    assert row.status == "failed"
    assert "must-not-persist" not in row.last_error_message

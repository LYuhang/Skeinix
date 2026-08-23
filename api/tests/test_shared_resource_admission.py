"""Recipient projection admission must narrow RLS to one shared root."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from starlette.requests import Request

from vibecanvas_api.auth.deps import AuthContext, _admit_shared_resource
from vibecanvas_api.storage.db import session_scope


@pytest.mark.asyncio
async def test_projection_rebinds_rls_and_records_exact_admitted_root(pg_engine):
    owner_id = uuid.uuid4()
    recipient_tenant_id = uuid.uuid4()
    recipient_user_id = uuid.uuid4()
    mutation_id = uuid.uuid4()
    async with pg_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO tenants(tenant_id, name) VALUES "
                "(:owner_id, 'Owner'), (:recipient_id, 'Recipient')"
            ),
            {
                "owner_id": owner_id,
                "recipient_id": recipient_tenant_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO users(
                    user_id, tenant_id, email, display_name, status
                ) VALUES (
                    :user_id, :tenant_id, :email, '', 'active'
                )
                """
            ),
            {
                "user_id": recipient_user_id,
                "tenant_id": recipient_tenant_id,
                "email": f"redacted-{recipient_user_id}@invalid.local",
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO authz_mutations(
                    mutation_id, tenant_id, actor_type, actor_id, kind,
                    operation, desired_state, object_type, object_id,
                    relation, subject_type, subject_id, edge_revision,
                    status, revocation_guard_active, idempotency_key,
                    attempt_count, requested_at, applied_at
                ) VALUES (
                    :mutation_id, :owner_id, 'system', 'test',
                    'direct_binding', 'write', 'present', 'workflow',
                    'wf-shared', 'viewer', 'user', :user_id, 1,
                    'applied', false, :idempotency_key, 0, now(), now()
                )
                """
            ),
            {
                "mutation_id": mutation_id,
                "owner_id": owner_id,
                "user_id": str(recipient_user_id),
                "idempotency_key": f"test-{mutation_id}",
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO shared_resource_projections(
                    owner_tenant_id, resource_type, resource_id,
                    recipient_user_id, relation, source_mutation_id,
                    edge_revision
                ) VALUES (
                    :owner_id, 'workflow', 'wf-shared', :user_id,
                    'viewer', :mutation_id, 1
                )
                """
            ),
            {
                "owner_id": owner_id,
                "user_id": recipient_user_id,
                "mutation_id": mutation_id,
            },
        )

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/workflows/wf-shared",
        "headers": [],
        "query_string": b"",
        "path_params": {"wf_id": "wf-shared"},
    })
    auth = AuthContext(
        user_id=str(recipient_user_id),
        tenant_id=str(recipient_tenant_id),
        email="recipient@example.test",
        active_organization_id=str(recipient_tenant_id),
        membership_status="active",
    )
    async with session_scope(
        tenant_id=str(recipient_tenant_id),
        user_id=str(recipient_user_id),
    ) as session:
        await _admit_shared_resource(request, auth, session)
        current_tenant = (
            await session.execute(
                text("SELECT current_setting('app.tenant_id', true)")
            )
        ).scalar_one()

    assert current_tenant == str(owner_id)
    assert request.state.admitted_resource_organization_id == str(owner_id)
    assert request.state.admitted_resource_type == "workflow"
    assert request.state.admitted_resource_id == "wf-shared"

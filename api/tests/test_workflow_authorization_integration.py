"""Workflow HTTP authorization, capabilities, sharing, and revoke matrix."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from vibecanvas_api.app import build_app
from vibecanvas_api.authorization.openfga_client import (
    OpenFgaReadPage,
    OpenFgaTuple,
)
from vibecanvas_api.config import config


class _RelationshipStore:
    """Small tuple evaluator; the pinned model itself is tested separately."""

    def __init__(self) -> None:
        self.tuples: set[OpenFgaTuple] = set()

    async def read(self, *, tuple_key, **_kwargs):
        return OpenFgaReadPage(
            (tuple_key,) if tuple_key in self.tuples else ()
        )

    async def write(self, *, writes=(), deletes=()):
        self.tuples.update(writes)
        self.tuples.difference_update(deletes)

    async def batch_check(self, checks, **_kwargs):
        return tuple(
            self._allowed(user, relation, object_)
            for user, relation, object_ in checks
        )

    async def list_objects(
        self,
        *,
        user,
        relation,
        object_type,
        **_kwargs,
    ):
        objects = {
            item.object
            for item in self.tuples
            if item.object.startswith(f"{object_type}:")
        }
        return tuple(
            object_.split(":", 1)[1]
            for object_ in sorted(objects)
            if self._allowed(user, relation, object_)
        )

    def _has(self, user: str, relation: str, object_: str) -> bool:
        return OpenFgaTuple(user, relation, object_) in self.tuples

    def _organization_for(self, object_: str) -> str | None:
        for item in self.tuples:
            if item.object == object_ and item.relation == "organization":
                return item.user
        return None

    def _organization_role(
        self,
        user: str,
        object_: str,
        roles: set[str],
    ) -> bool:
        organization = self._organization_for(object_)
        return bool(
            organization
            and any(self._has(user, role, organization) for role in roles)
        )

    def _role(self, user: str, object_: str, roles: set[str]) -> bool:
        return any(self._has(user, role, object_) for role in roles)

    def _allowed(self, user: str, relation: str, object_: str) -> bool:
        if object_.startswith("organization:"):
            if relation == "can_create_resource":
                return self._role(
                    user,
                    object_,
                    {"owner", "admin", "member"},
                )
            if relation == "can_view_metadata":
                return self._role(
                    user,
                    object_,
                    {"owner", "admin", "member", "auditor"},
                )
            return False
        if not object_.startswith("workflow:"):
            return False
        content_roles = {"viewer", "editor", "operator", "manager"}
        if relation == "can_view_metadata":
            return self._role(user, object_, content_roles) or (
                self._organization_role(
                    user,
                    object_,
                    {"owner", "admin", "auditor"},
                )
            )
        if relation in {"can_view", "can_export", "can_use", "can_mount"}:
            return self._role(user, object_, content_roles)
        if relation == "can_update":
            return self._role(user, object_, {"editor", "manager"})
        if relation in {
            "can_execute",
            "can_cancel",
            "can_inspect_runs",
        }:
            return self._role(user, object_, {"operator", "manager"})
        if relation == "can_deploy":
            return self._role(user, object_, {"manager"})
        if relation in {"can_delete", "can_manage_access"}:
            return self._role(user, object_, {"manager"}) or (
                self._organization_role(
                    user,
                    object_,
                    {"owner", "admin"},
                )
            )
        return False


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


async def _register(client: AsyncClient, label: str) -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{label}_{uuid.uuid4().hex[:12]}@example.com",
            "username": label,
            "password": "pw12345678",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["session_token"]
    me = (
        await client.get(
            "/api/v1/auth/me",
            headers=_headers(token),
        )
    ).json()
    return token, me


async def _join_active_organization(
    pg_engine,
    *,
    user_id: str,
    organization_id: str,
    role: str = "member",
) -> None:
    async with pg_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO org_memberships(
                    membership_id, user_id, tenant_id, org_role, status
                ) VALUES (
                    gen_random_uuid(), :user_id, :organization_id,
                    :role, 'active'
                )
                """
            ),
            {
                "user_id": uuid.UUID(user_id),
                "organization_id": uuid.UUID(organization_id),
                "role": role,
            },
        )
        await connection.execute(
            text(
                """
                UPDATE sessions
                SET tenant_id = :organization_id,
                    active_organization_id = :organization_id,
                    generation = generation + 1
                WHERE user_id = :user_id
                """
            ),
            {
                "user_id": uuid.UUID(user_id),
                "organization_id": uuid.UUID(organization_id),
            },
        )


@pytest.mark.asyncio
async def test_workflow_direct_share_capabilities_and_revoke(
    pg_engine,
    monkeypatch,
):
    monkeypatch.setattr(config, "resource_sharing_enabled", True)
    store = _RelationshipStore()
    app = build_app()
    app.state.openfga_client = store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        owner_token, owner = await _register(client, "owner")
        viewer_token, viewer = await _register(client, "viewer")
        outsider_token, outsider = await _register(client, "outsider")
        guest_token, guest = await _register(client, "guest")
        auditor_token, auditor = await _register(client, "auditor")
        admin_token, admin = await _register(client, "admin")
        await _join_active_organization(
            pg_engine,
            user_id=viewer["user_id"],
            organization_id=owner["tenant_id"],
        )
        await _join_active_organization(
            pg_engine,
            user_id=outsider["user_id"],
            organization_id=owner["tenant_id"],
        )
        await _join_active_organization(
            pg_engine,
            user_id=guest["user_id"],
            organization_id=owner["tenant_id"],
            role="guest",
        )
        await _join_active_organization(
            pg_engine,
            user_id=auditor["user_id"],
            organization_id=owner["tenant_id"],
            role="auditor",
        )
        await _join_active_organization(
            pg_engine,
            user_id=admin["user_id"],
            organization_id=owner["tenant_id"],
            role="admin",
        )
        store.tuples.add(OpenFgaTuple(
            f"user:{guest['user_id']}",
            "guest",
            f"organization:{owner['tenant_id']}",
        ))
        store.tuples.add(OpenFgaTuple(
            f"user:{auditor['user_id']}",
            "auditor",
            f"organization:{owner['tenant_id']}",
        ))
        store.tuples.add(OpenFgaTuple(
            f"user:{admin['user_id']}",
            "admin",
            f"organization:{owner['tenant_id']}",
        ))

        guest_create = await client.post(
            "/api/v1/workflows",
            json={"name": "Guest cannot create"},
            headers=_headers(guest_token),
        )
        assert guest_create.status_code == 404

        created = await client.post(
            "/api/v1/workflows",
            json={
                "name": "Shared workflow",
                "description": "private workflow description",
                "tags": ["private-tag"],
            },
            headers=_headers(owner_token),
        )
        assert created.status_code == 201, created.text
        workflow = created.json()
        wf_id = workflow["wf_id"]
        assert workflow["access"]["effective_role"] == "manager"
        assert "manage_access" in workflow["access"]["capabilities"]

        # Auditors and organization admins can inventory resources without
        # receiving private workflow content.  Their list projection contains
        # only safe inventory metadata and the detail endpoint remains hidden.
        for token, expected_admin_capabilities in (
            (auditor_token, False),
            (admin_token, True),
        ):
            metadata = await client.get(
                "/api/v1/workflows",
                headers=_headers(token),
            )
            assert metadata.status_code == 200, metadata.text
            assert [item["wf_id"] for item in metadata.json()["items"]] == [wf_id]
            item = metadata.json()["items"][0]
            assert item["workflow_name"] == "Shared workflow"
            assert item["description"] == ""
            assert item["tags"] == []
            assert "view_metadata" in item["access"]["capabilities"]
            assert "view" not in item["access"]["capabilities"]
            assert (
                "manage_access" in item["access"]["capabilities"]
            ) is expected_admin_capabilities
            hidden_detail = await client.get(
                f"/api/v1/workflows/{wf_id}",
                headers=_headers(token),
            )
            assert hidden_detail.status_code == 404

        guest_empty_list = await client.get(
            "/api/v1/workflows",
            headers=_headers(guest_token),
        )
        assert guest_empty_list.status_code == 200
        assert guest_empty_list.json()["items"] == []

        empty_list = await client.get(
            "/api/v1/workflows",
            headers=_headers(viewer_token),
        )
        assert empty_list.status_code == 200
        assert empty_list.json()["items"] == []

        binding = {
            "relation": "viewer",
            "subject_type": "user",
            "subject_id": viewer["user_id"],
            "subject_relation": None,
        }
        granted = await client.post(
            f"/api/v1/workflows/{wf_id}/access",
            json=binding,
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "grant-viewer-1"},
            ),
        )
        assert granted.status_code == 201, granted.text

        visible = await client.get(
            "/api/v1/workflows",
            headers=_headers(viewer_token),
        )
        assert visible.status_code == 200, visible.text
        assert [item["wf_id"] for item in visible.json()["items"]] == [wf_id]
        access = visible.json()["items"][0]["access"]
        assert access["effective_role"] == "viewer"
        assert "view" in access["capabilities"]
        assert "update" not in access["capabilities"]

        guest_binding = {
            **binding,
            "subject_id": guest["user_id"],
        }
        guest_granted = await client.post(
            f"/api/v1/workflows/{wf_id}/access",
            json=guest_binding,
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "grant-guest-viewer-1"},
            ),
        )
        assert guest_granted.status_code == 201, guest_granted.text
        guest_visible = await client.get(
            f"/api/v1/workflows/{wf_id}",
            headers=_headers(guest_token),
        )
        assert guest_visible.status_code == 200, guest_visible.text
        assert guest_visible.json()["meta"]["description"] == (
            "private workflow description"
        )

        detail = await client.get(
            f"/api/v1/workflows/{wf_id}",
            headers=_headers(viewer_token),
        )
        assert detail.status_code == 200, detail.text
        denied_update = await client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "not allowed"},
            headers=_headers(viewer_token),
        )
        assert denied_update.status_code == 404
        outsider_detail = await client.get(
            f"/api/v1/workflows/{wf_id}",
            headers=_headers(outsider_token),
        )
        assert outsider_detail.status_code == 404

        listed = await client.get(
            f"/api/v1/workflows/{wf_id}/access",
            headers=_headers(owner_token),
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == [
            {**binding, "source": "direct"},
            {**guest_binding, "source": "direct"},
        ]

        foreign_token, foreign = await _register(client, "foreign")
        foreign_grant = await client.post(
            f"/api/v1/workflows/{wf_id}/access",
            json={**binding, "subject_id": foreign["user_id"]},
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "foreign-user-denied"},
            ),
        )
        assert foreign_grant.status_code == 422
        assert foreign_token

        structural_revoke = await client.request(
            "DELETE",
            f"/api/v1/workflows/{wf_id}/access",
            json={
                "relation": "manager",
                "subject_type": "user",
                "subject_id": owner["user_id"],
                "subject_relation": None,
            },
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "cannot-revoke-creator"},
            ),
        )
        assert structural_revoke.status_code == 409

        revoked = await client.request(
            "DELETE",
            f"/api/v1/workflows/{wf_id}/access",
            json=binding,
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "revoke-viewer-1"},
            ),
        )
        assert revoked.status_code == 200, revoked.text
        after_revoke = await client.get(
            f"/api/v1/workflows/{wf_id}",
            headers=_headers(viewer_token),
        )
        assert after_revoke.status_code == 404
        after_revoke_list = await client.get(
            "/api/v1/workflows",
            headers=_headers(viewer_token),
        )
        assert after_revoke_list.json()["items"] == []

        guest_revoked = await client.request(
            "DELETE",
            f"/api/v1/workflows/{wf_id}/access",
            json=guest_binding,
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "revoke-guest-viewer-1"},
            ),
        )
        assert guest_revoked.status_code == 200, guest_revoked.text
        guest_after_revoke = await client.get(
            f"/api/v1/workflows/{wf_id}",
            headers=_headers(guest_token),
        )
        assert guest_after_revoke.status_code == 404
        guest_after_revoke_list = await client.get(
            "/api/v1/workflows",
            headers=_headers(guest_token),
        )
        assert guest_after_revoke_list.status_code == 200
        assert guest_after_revoke_list.json()["items"] == []

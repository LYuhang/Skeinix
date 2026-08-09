"""Skill installation authorization, revision inheritance, and Runtime use."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from vibecanvas_api.app import build_app
from vibecanvas_api.authorization.dependencies import (
    authz_service_for_session,
)
from vibecanvas_api.authorization.openfga_client import (
    OpenFgaReadPage,
    OpenFgaTuple,
)
from vibecanvas_api.authorization.types import (
    AuthzRequestContext,
    PrincipalRef,
    PrincipalType,
)
from vibecanvas_api.config import config
from vibecanvas_api.services.runtime_skills import (
    hydrate_runtime_skills,
    runtime_skill_descriptors,
)
from vibecanvas_api.storage.db import session_scope


SKILL_MD = (
    "---\n"
    "name: shared-greet\n"
    "description: shared greeting playbook\n"
    "allowed-tools: [bash]\n"
    "version: 1\n"
    "---\n"
    "# Playbook\n"
    "Say hello using the shared reference."
)


class _RelationshipStore:
    def __init__(self) -> None:
        self.tuples: set[OpenFgaTuple] = set()

    async def read(self, *, tuple_key, **_kwargs):
        return OpenFgaReadPage(
            (tuple_key,) if tuple_key in self.tuples else ()
        )

    async def write(self, *, writes=(), deletes=()):
        self.tuples.update(writes)
        self.tuples.difference_update(deletes)

    async def close(self) -> None:
        return None

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

    def _role(self, user: str, object_: str, roles: set[str]) -> bool:
        return any(self._has(user, role, object_) for role in roles)

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

    def _allowed(self, user: str, relation: str, object_: str) -> bool:
        if object_.startswith("organization:"):
            return (
                relation == "can_create_resource"
                and self._role(
                    user,
                    object_,
                    {"owner", "admin", "member"},
                )
            )
        if not object_.startswith("skill_installation:"):
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
        if relation == "can_view":
            return self._role(user, object_, content_roles)
        if relation == "can_update":
            return self._role(user, object_, {"editor", "manager"})
        if relation == "can_use":
            return self._role(user, object_, {"operator", "manager"})
        if relation == "can_publish":
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


def _bundle() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", SKILL_MD)
        archive.writestr("references/example.txt", "shared reference")
    return output.getvalue()


async def _register(
    client: AsyncClient,
    label: str,
) -> tuple[str, dict]:
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
async def test_skill_roles_revision_runtime_use_and_revoke(
    pg_engine,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(config, "resource_sharing_enabled", True)
    store = _RelationshipStore()
    app = build_app()
    app.state.openfga_client = store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        owner_token, owner = await _register(client, "skill_owner")
        viewer_token, viewer = await _register(client, "skill_viewer")
        editor_token, editor = await _register(client, "skill_editor")
        operator_token, operator = await _register(
            client,
            "skill_operator",
        )
        outsider_token, outsider = await _register(
            client,
            "skill_outsider",
        )
        guest_token, guest = await _register(client, "skill_guest")
        auditor_token, auditor = await _register(client, "skill_auditor")
        admin_token, admin = await _register(client, "skill_admin")
        for member in (viewer, editor, operator, outsider):
            await _join_active_organization(
                pg_engine,
                user_id=member["user_id"],
                organization_id=owner["tenant_id"],
            )
        await _join_active_organization(
            pg_engine,
            user_id=guest["user_id"],
            organization_id=owner["tenant_id"],
            role="guest",
        )
        store.tuples.add(OpenFgaTuple(
            f"user:{guest['user_id']}",
            "guest",
            f"organization:{owner['tenant_id']}",
        ))
        for role, member in (("auditor", auditor), ("admin", admin)):
            await _join_active_organization(
                pg_engine,
                user_id=member["user_id"],
                organization_id=owner["tenant_id"],
                role=role,
            )
            store.tuples.add(OpenFgaTuple(
                f"user:{member['user_id']}",
                role,
                f"organization:{owner['tenant_id']}",
            ))

        guest_create = await client.post(
            "/api/v1/skills/custom",
            files={
                "bundle": (
                    "guest.zip",
                    _bundle(),
                    "application/zip",
                ),
            },
            headers=_headers(guest_token),
        )
        assert guest_create.status_code == 404

        created = await client.post(
            "/api/v1/skills/custom",
            files={
                "bundle": (
                    "shared-greet.zip",
                    _bundle(),
                    "application/zip",
                ),
            },
            headers=_headers(owner_token),
        )
        assert created.status_code == 201, created.text
        skill = created.json()
        skill_id = skill["id"]
        assert skill["access"]["effective_role"] == "manager"
        assert "manage_access" in skill["access"]["capabilities"]
        assert OpenFgaTuple(
            f"organization:{owner['tenant_id']}",
            "organization",
            f"skill_installation:{skill_id}",
        ) in store.tuples

        for token, is_admin in (
            (auditor_token, False),
            (admin_token, True),
        ):
            inventory = await client.get(
                "/api/v1/skills",
                headers=_headers(token),
            )
            assert inventory.status_code == 200, inventory.text
            item = inventory.json()["items"][0]
            assert item["id"] == skill_id
            assert item["name"] == "shared-greet"
            capabilities = set(item["access"]["capabilities"])
            assert "view_metadata" in capabilities
            assert "view" not in capabilities
            assert "use" not in capabilities
            assert "publish" not in capabilities
            assert ("manage_access" in capabilities) is is_admin
            assert (
                await client.get(
                    f"/api/v1/skills/{skill_id}",
                    headers=_headers(token),
                )
            ).status_code == 404
        assert OpenFgaTuple(
            f"user:{owner['user_id']}",
            "manager",
            f"skill_installation:{skill_id}",
        ) in store.tuples

        assert (
            await client.get(
                "/api/v1/skills",
                headers=_headers(viewer_token),
            )
        ).json()["items"] == []
        assert (
            await client.get(
                f"/api/v1/skills/{skill_id}",
                headers=_headers(outsider_token),
            )
        ).status_code == 404

        async def grant(relation: str, subject_id: str) -> None:
            response = await client.post(
                f"/api/v1/skills/{skill_id}/access",
                json={
                    "relation": relation,
                    "subject_type": "user",
                    "subject_id": subject_id,
                    "subject_relation": None,
                },
                headers=_headers(
                    owner_token,
                    **{
                        "Idempotency-Key": (
                            f"grant-skill-{relation}-{subject_id}"
                        )
                    },
                ),
            )
            assert response.status_code == 201, response.text

        await grant("viewer", viewer["user_id"])
        await grant("editor", editor["user_id"])
        await grant("operator", operator["user_id"])

        viewer_list = await client.get(
            "/api/v1/skills",
            headers=_headers(viewer_token),
        )
        assert [item["id"] for item in viewer_list.json()["items"]] == [
            skill_id
        ]
        viewer_access = viewer_list.json()["items"][0]["access"]
        assert viewer_access["effective_role"] == "viewer"
        assert "view" in viewer_access["capabilities"]
        assert "update" not in viewer_access["capabilities"]
        assert "use" not in viewer_access["capabilities"]

        versions = await client.get(
            f"/api/v1/skills/{skill_id}/versions",
            headers=_headers(viewer_token),
        )
        assert versions.status_code == 200, versions.text
        revision = versions.json()[0]
        assert revision["access"]["effective_role"] == "viewer"
        historical_file = await client.get(
            f"/api/v1/skills/{skill_id}/versions/"
            f"{revision['revision_id']}/files/references/example.txt",
            headers=_headers(viewer_token),
        )
        assert historical_file.text == "shared reference"

        draft_md = SKILL_MD.replace(
            "Say hello using the shared reference.",
            "Say hello after reading the shared reference.",
        )
        viewer_save = await client.put(
            f"/api/v1/skills/{skill_id}/draft",
            json={"skill_md": draft_md},
            headers=_headers(viewer_token),
        )
        assert viewer_save.status_code == 404
        editor_save = await client.put(
            f"/api/v1/skills/{skill_id}/draft",
            json={"skill_md": draft_md},
            headers=_headers(editor_token),
        )
        assert editor_save.status_code == 200, editor_save.text
        assert editor_save.json()["access"]["effective_role"] == "editor"
        editor_publish = await client.post(
            f"/api/v1/skills/{skill_id}/versions",
            json={"version": 2},
            headers=_headers(editor_token),
        )
        assert editor_publish.status_code == 404
        owner_publish = await client.post(
            f"/api/v1/skills/{skill_id}/versions",
            json={"version": 2},
            headers=_headers(owner_token),
        )
        assert owner_publish.status_code == 200, owner_publish.text

        async with session_scope(
            tenant_id=owner["tenant_id"],
        ) as session:
            service = authz_service_for_session(
                session=session,
                organization_id=owner["tenant_id"],
                openfga_client=store,
            )
            operator_skills = await runtime_skill_descriptors(
                session=session,
                service=service,
                principal=PrincipalRef(
                    PrincipalType.USER,
                    operator["user_id"],
                ),
                context=AuthzRequestContext(
                    active_organization_id=owner["tenant_id"],
                    membership_role="member",
                    membership_status="active",
                ),
            )
            viewer_skills = await runtime_skill_descriptors(
                session=session,
                service=service,
                principal=PrincipalRef(
                    PrincipalType.USER,
                    viewer["user_id"],
                ),
                context=AuthzRequestContext(
                    active_organization_id=owner["tenant_id"],
                    membership_role="member",
                    membership_status="active",
                ),
            )
        assert [item.skill_id for item in operator_skills] == [skill_id]
        assert viewer_skills == []

        runtime_root = tmp_path / "runtime-skills"
        hydrated = await hydrate_runtime_skills(
            destination=str(runtime_root),
            tenant_id=owner["tenant_id"],
            skills=operator_skills,
        )
        assert hydrated == 2
        assert (
            "Say hello after reading"
            in (
                runtime_root / skill_id / "SKILL.md"
            ).read_text()
        )

        listed = await client.get(
            f"/api/v1/skills/{skill_id}/access",
            headers=_headers(owner_token),
        )
        assert listed.status_code == 200, listed.text
        assert {
            (item["relation"], item["subject_id"])
            for item in listed.json()["items"]
        } == {
            ("viewer", viewer["user_id"]),
            ("editor", editor["user_id"]),
            ("operator", operator["user_id"]),
        }

        structural_revoke = await client.request(
            "DELETE",
            f"/api/v1/skills/{skill_id}/access",
            json={
                "relation": "manager",
                "subject_type": "user",
                "subject_id": owner["user_id"],
                "subject_relation": None,
            },
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "cannot-revoke-skill-creator"},
            ),
        )
        assert structural_revoke.status_code == 409

        operator_binding = {
            "relation": "operator",
            "subject_type": "user",
            "subject_id": operator["user_id"],
            "subject_relation": None,
        }
        revoked = await client.request(
            "DELETE",
            f"/api/v1/skills/{skill_id}/access",
            json=operator_binding,
            headers=_headers(
                owner_token,
                **{"Idempotency-Key": "revoke-skill-operator"},
            ),
        )
        assert revoked.status_code == 200, revoked.text
        async with session_scope(
            tenant_id=owner["tenant_id"],
        ) as session:
            service = authz_service_for_session(
                session=session,
                organization_id=owner["tenant_id"],
                openfga_client=store,
            )
            assert await runtime_skill_descriptors(
                session=session,
                service=service,
                principal=PrincipalRef(
                    PrincipalType.USER,
                    operator["user_id"],
                ),
                context=AuthzRequestContext(
                    active_organization_id=owner["tenant_id"],
                    membership_role="member",
                    membership_status="active",
                ),
            ) == []

        deleted = await client.delete(
            f"/api/v1/skills/{skill_id}",
            headers=_headers(owner_token),
        )
        assert deleted.status_code == 204, deleted.text
        assert OpenFgaTuple(
            f"organization:{owner['tenant_id']}",
            "organization",
            f"skill_installation:{skill_id}",
        ) not in store.tuples
        assert OpenFgaTuple(
            f"user:{owner['user_id']}",
            "manager",
            f"skill_installation:{skill_id}",
        ) not in store.tuples
        assert (
            await client.get(
                "/api/v1/skills",
                headers=_headers(editor_token),
            )
        ).json()["items"] == []

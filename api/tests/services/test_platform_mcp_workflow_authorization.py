"""Workflow Platform MCP uses the same OpenFGA action matrix as HTTP."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from vibecanvas_api.agents.tools.decorator import ToolError
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.authorization.openfga_client import (
    OpenFgaReadPage,
    OpenFgaTuple,
)
from vibecanvas_api.services.platform_mcp.authorization import (
    create_authorized_workflow,
    list_authorized_workflows,
    prepare_platform_workflow_tool,
    require_organization_create,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models_org import OrgMembership
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


pytestmark = pytest.mark.asyncio


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

    def _roles(
        self,
        user: str,
        object_: str,
        roles: set[str],
    ) -> bool:
        return any(self._has(user, role, object_) for role in roles)

    def _organization_for(self, object_: str) -> str | None:
        return next(
            (
                item.user
                for item in self.tuples
                if item.object == object_
                and item.relation == "organization"
            ),
            None,
        )

    def _organization_roles(
        self,
        user: str,
        object_: str,
        roles: set[str],
    ) -> bool:
        organization = self._organization_for(object_)
        return bool(
            organization
            and self._roles(user, organization, roles)
        )

    def _allowed(self, user: str, relation: str, object_: str) -> bool:
        if object_.startswith("organization:"):
            if relation == "can_create_resource":
                return self._roles(
                    user,
                    object_,
                    {"owner", "admin", "member"},
                )
            if relation == "can_view_metadata":
                return self._roles(
                    user,
                    object_,
                    {"owner", "admin", "member", "auditor"},
                )
            return False
        if not object_.startswith("workflow:"):
            return False
        content_roles = {"viewer", "editor", "operator", "manager"}
        if relation == "can_view_metadata":
            return self._roles(user, object_, content_roles) or (
                self._organization_roles(
                    user,
                    object_,
                    {"owner", "admin", "auditor"},
                )
            )
        if relation in {"can_view", "can_export", "can_use", "can_mount"}:
            return self._roles(user, object_, content_roles)
        if relation == "can_update":
            return self._roles(user, object_, {"editor", "manager"})
        if relation in {
            "can_execute",
            "can_cancel",
            "can_inspect_runs",
        }:
            return self._roles(user, object_, {"operator", "manager"})
        if relation == "can_deploy":
            return self._roles(user, object_, {"manager"})
        if relation in {"can_delete", "can_manage_access"}:
            return self._roles(user, object_, {"manager"}) or (
                self._organization_roles(
                    user,
                    object_,
                    {"owner", "admin"},
                )
            )
        return False


def _context(
    *,
    organization_id: str,
    user_id: str,
    store: _RelationshipStore,
    role: str = "member",
    workflow_id: str | None = None,
):
    return SimpleNamespace(
        tenant_id=organization_id,
        username=user_id,
        turn_id="turn-platform-authz",
        current_workflow_id=workflow_id,
        authorization_client=store,
        authorization_membership_id=str(uuid.uuid4()),
        authorization_membership_role=role,
        authorization_membership_status="active",
        authorization_session_generation=1,
        authorization_authentication_strength="platform_mcp_capability",
        workflow={},
    )


async def _user(label: str):
    async with session_scope() as session:
        return await AuthRepo(session).register(
            f"{label}-{uuid.uuid4().hex}@example.com",
            "not-a-real-password-hash",
            label,
        )


async def test_platform_mcp_workflow_permission_matrix_and_create(
    pg_engine,
    monkeypatch,
):
    store = _RelationshipStore()
    owner = await _user("platform-owner")
    organization_id = str(owner.tenant_id)
    owner_id = str(owner.user_id)
    workflow_id = f"wf-{uuid.uuid4().hex}"

    users: dict[str, str] = {}
    for relation in ("viewer", "editor", "operator", "none", "guest"):
        row = await _user(f"platform-{relation}")
        users[relation] = str(row.user_id)
        async with session_scope(organization_id) as session:
            session.add(OrgMembership(
                tenant_id=owner.tenant_id,
                user_id=row.user_id,
                org_role="guest" if relation == "guest" else "member",
                status="active",
            ))

    async with session_scope(organization_id) as session:
        repo = WorkflowRepo(session, owner_id)
        await repo.create_workflow(
            wf_id=workflow_id,
            name="Platform permission matrix",
            creator_user_id=owner_id,
        )
        await repo.commit(
            workflow_id,
            {"node_1": {"node_id": "node_1", "node_type": "Prompt"}},
            note="permission matrix fixture",
        )

    organization_object = f"organization:{organization_id}"
    workflow_object = f"workflow:{workflow_id}"
    store.tuples.update({
        OpenFgaTuple(f"user:{owner_id}", "owner", organization_object),
        OpenFgaTuple(
            f"organization:{organization_id}",
            "organization",
            workflow_object,
        ),
        OpenFgaTuple(f"user:{owner_id}", "manager", workflow_object),
        OpenFgaTuple(
            f"user:{users['viewer']}",
            "viewer",
            workflow_object,
        ),
        OpenFgaTuple(
            f"user:{users['editor']}",
            "editor",
            workflow_object,
        ),
        OpenFgaTuple(
            f"user:{users['operator']}",
            "operator",
            workflow_object,
        ),
        OpenFgaTuple(
            f"user:{users['guest']}",
            "guest",
            organization_object,
        ),
    })

    viewer = _context(
        organization_id=organization_id,
        user_id=users["viewer"],
        store=store,
        workflow_id=workflow_id,
    )
    listed = await list_authorized_workflows(viewer)
    assert [item["wf_id"] for item in listed] == [workflow_id]
    assert listed[0]["access"]["effective_role"] == "viewer"
    assert "view" in listed[0]["access"]["capabilities"]
    await prepare_platform_workflow_tool(
        viewer,
        server="workflow",
        tool_name="get_workflow",
        arguments={},
    )
    await prepare_platform_workflow_tool(
        viewer,
        server="build",
        tool_name="set_workflow",
        arguments={"workflow_id": workflow_id},
    )
    with pytest.raises(ToolError, match="permission_denied"):
        await prepare_platform_workflow_tool(
            viewer,
            server="build",
            tool_name="update_canvas",
            arguments={},
        )

    editor = _context(
        organization_id=organization_id,
        user_id=users["editor"],
        store=store,
        workflow_id=workflow_id,
    )
    await prepare_platform_workflow_tool(
        editor,
        server="build",
        tool_name="update_canvas",
        arguments={},
    )
    assert editor.workflow
    with pytest.raises(ToolError, match="permission_denied"):
        await prepare_platform_workflow_tool(
            editor,
            server="build",
            tool_name="run_workflow",
            arguments={},
        )

    operator = _context(
        organization_id=organization_id,
        user_id=users["operator"],
        store=store,
        workflow_id=workflow_id,
    )
    await prepare_platform_workflow_tool(
        operator,
        server="build",
        tool_name="node_execute",
        arguments={"node": "node_1"},
    )
    with pytest.raises(ToolError, match="permission_denied"):
        await prepare_platform_workflow_tool(
            operator,
            server="build",
            tool_name="new_version",
            arguments={},
        )

    no_grant = _context(
        organization_id=organization_id,
        user_id=users["none"],
        store=store,
        workflow_id=workflow_id,
    )
    assert await list_authorized_workflows(no_grant) == []
    with pytest.raises(ToolError, match="permission_denied"):
        await prepare_platform_workflow_tool(
            no_grant,
            server="workflow",
            tool_name="get_workflow",
            arguments={},
        )

    owner_context = _context(
        organization_id=organization_id,
        user_id=owner_id,
        store=store,
        role="owner",
        workflow_id=workflow_id,
    )
    await require_organization_create(owner_context)
    created = await create_authorized_workflow(
        owner_context,
        name="Created through Platform MCP",
        description="Authorization-projected",
    )
    created_id = str(created.meta["wf_id"])
    assert OpenFgaTuple(
        f"organization:{organization_id}",
        "organization",
        f"workflow:{created_id}",
    ) in store.tuples
    assert OpenFgaTuple(
        f"user:{owner_id}",
        "manager",
        f"workflow:{created_id}",
    ) in store.tuples

    guest = _context(
        organization_id=organization_id,
        user_id=users["guest"],
        store=store,
        role="guest",
    )
    with pytest.raises(ToolError, match="permission_denied"):
        await require_organization_create(guest)

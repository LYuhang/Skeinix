"""Executable Diagram MCP contract fixtures derived from live registries."""

import base64
import json
import uuid
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import text

from vibecanvas_api.agent import AgentContext
from vibecanvas_api.diagrams.mcp_contract import (
    DIAGRAM_INPUT_SCHEMAS,
    DIAGRAM_OUTPUT_SCHEMAS,
)
from vibecanvas_api.diagrams.models import (
    ElementsConstraint,
    InsideConstraint,
    RelativeConstraint,
    RouteConstraint,
)
from vibecanvas_api.diagrams.registry import ASSET_CATALOG, list_enabled_types
from vibecanvas_api.diagrams.validator import parse_and_validate
from vibecanvas_api.services.chat_workspace import chat_workspace_scope_id
from vibecanvas_api.services.platform_mcp.diagram_tools.tools import (
    _constraint_node_ids,
    _review_issue_is_actionable,
    check_diagram,
    export_diagram,
    get_diagram_spec,
    inspect_diagram,
    present_diagram,
    read_diagram_review_image,
    review_diagram,
    search_diagram_assets,
)
from vibecanvas_api.services.platform_mcp.server import _required_tool_actions
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.sync_session import current_sync_tenant_id
from vibecanvas_api.storage.vfs_store import PostgresVfsStore
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        (ElementsConstraint(type="same-rank", elements=["a", "b"]), {"a", "b"}),
        (RelativeConstraint(type="left-of", element="a", target="b"), {"a", "b"}),
        (InsideConstraint(type="inside", element="a", container="group"), {"a", "group"}),
        (RouteConstraint(type="route-above", edge="edge-a-b", element="c"), {"c"}),
    ],
)
def test_constraint_projection_handles_every_registered_shape(
    constraint, expected
) -> None:
    assert _constraint_node_ids(constraint) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", list_enabled_types(), ids=lambda item: item.key)
async def test_each_enabled_type_returns_a_self_contained_valid_spec(spec):
    content, _artifact = await get_diagram_spec.coroutine(
        family=spec.family,
        diagram_type=spec.type,
        schema_version=1,
        runtime=SimpleNamespace(context=None),
    )
    result = json.loads(content)

    Draft202012Validator(DIAGRAM_OUTPUT_SCHEMAS["get_diagram_spec"]).validate(
        result
    )
    Draft202012Validator(result["authoring_schema"]).validate(
        result["minimal_example"]
    )
    document, issues = parse_and_validate(
        json.dumps(result["minimal_example"], ensure_ascii=False)
    )
    assert document is not None
    assert issues == []
    assert 5 <= len(result["authoring_instructions"]) <= 12
    assert result["spec_ref"]["spec_hash"] == spec.spec_hash


@pytest.mark.asyncio
async def test_every_advertised_asset_key_resolves_from_the_shared_catalog():
    content, _artifact = await search_diagram_assets.coroutine(
        query="",
        family="architecture",
        diagram_type="system-container",
        asset_kinds=[],
        limit=20,
        runtime=SimpleNamespace(context=None),
    )
    result = json.loads(content)

    Draft202012Validator(
        DIAGRAM_OUTPUT_SCHEMAS["search_diagram_assets"]
    ).validate(result)
    assert {item["asset_key"] for item in result["assets"]} == set(
        ASSET_CATALOG
    )


def test_check_contract_exposes_explicit_deletion_allowlist() -> None:
    removed = DIAGRAM_INPUT_SCHEMAS["check_diagram"]["properties"][
        "removed_element_ids"
    ]
    assert removed["default"] == []
    assert removed["uniqueItems"] is True


def test_every_diagram_tool_has_a_capability_policy() -> None:
    for tool_name in DIAGRAM_INPUT_SCHEMAS:
        actions = _required_tool_actions("diagram", tool_name)
        assert "chat:execute" in actions
        assert "platform_mcp:call" in actions


def test_spec_ref_accepts_numeric_string_from_json_clients() -> None:
    schema = DIAGRAM_INPUT_SCHEMAS["check_diagram"]["properties"]["spec_ref"]
    value = {
        "schema_version": "1",
        "family": "flow",
        "type": "basic",
        "spec_version": "2026.08.1",
        "spec_hash": "sha256:" + "a" * 64,
    }
    Draft202012Validator(schema).validate(value)


def test_review_repair_request_is_valid_inspect_input() -> None:
    request = {
        "diagram_ref": {
            "path": "/data/diagrams/example.vdiagram.json",
            "revision": "sha256:" + "1" * 64,
            "source_hash": "sha256:" + "2" * 64,
            "bundle_hash": "sha256:" + "3" * 64,
            "scene_ref": "scene://sha256:" + "4" * 64,
            "compiler_version": "1.2.0",
            "theme_version": "1.0.0",
        },
        "selector": {"mode": "summary"},
        "include": [
            "semantics",
            "relations",
            "constraints",
            "ownership",
            "source_locations",
        ],
    }
    Draft202012Validator(
        DIAGRAM_INPUT_SCHEMAS["inspect_diagram"]
    ).validate(request)


def test_review_delivers_non_blocking_incremental_constraint_warning() -> None:
    constraint_warning = {
        "severity": "warning",
        "code": "constraint_unsatisfied",
        "disposition": "repairable",
    }
    assert not _review_issue_is_actionable(
        constraint_warning,
        preserve_layout=True,
    )
    assert _review_issue_is_actionable(constraint_warning)
    assert _review_issue_is_actionable({
        "severity": "warning",
        "code": "node_overlap",
        "disposition": "blocking",
    })
    assert _review_issue_is_actionable({
        "severity": "error",
        "code": "render_failed",
        "disposition": "blocking",
    })
    assert not _review_issue_is_actionable({
        "severity": "warning",
        "code": "ordinary_edge_crossing",
        "disposition": "render_cue",
    })


class _FakeDiagramSession:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.acknowledged: list[str] = []
        self.synced: list[str] = []
        self.sync_callback = None

    async def read_file(self, path: str) -> dict:
        data = self.files.get(path)
        if data is None:
            return {"ok": False}
        return {
            "ok": True,
            "kind": "text",
            "content": data.decode(),
        }

    async def acknowledge_external_vfs_commit(
        self,
        path: str,
        data: bytes,
    ) -> bool:
        self.files[path] = data
        self.acknowledged.append(path)
        return True

    async def mirror_vfs_write(self, path: str, data: bytes) -> bool:
        return await self.acknowledge_external_vfs_commit(path, data)

    async def write_bytes(self, path: str, data: bytes) -> dict:
        self.files[path] = data
        return {"ok": True, "bytes": len(data)}

    async def read_bytes(self, path: str) -> dict:
        data = self.files.get(path)
        if data is None:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": data, "bytes": len(data)}

    async def sync_workspace_path(self, path: str) -> bool:
        data = self.files.get(path)
        if data is None or self.sync_callback is None:
            return False
        saved = bool(self.sync_callback(path, data))
        if saved:
            self.synced.append(path)
        return saved


async def _call(tool, **arguments):
    content, artifact = await tool.coroutine(**arguments)
    assert artifact["status"] != "error", content
    return json.loads(content)


@pytest.mark.asyncio
async def test_create_modify_review_export_protocol_chain(
    pg_session,
    monkeypatch,
):
    from vibecanvas_api.services.platform_mcp.diagram_tools import tools

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await pg_session.execute(
        text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'diagram')"),
        {"t": tenant_id},
    )
    await pg_session.execute(
        text(
            "INSERT INTO users(user_id,tenant_id,email) "
            "VALUES (:u,:t,:e)"
        ),
        {
            "u": user_id,
            "t": tenant_id,
            "e": f"{user_id.hex[:8]}@example.test",
        },
    )
    await pg_session.execute(
        text("SELECT set_config('app.tenant_id',:t,false)"),
        {"t": str(tenant_id)},
    )
    workflow = await WorkflowRepo(
        pg_session,
        str(user_id),
    ).create_workflow(name="Diagram contract")
    chat_id = f"diagram-{uuid.uuid4().hex[:10]}"
    await ChatRepo(pg_session, str(user_id)).register_session(
        workflow["wf_id"],
        name="Diagram chat",
        chat_id=chat_id,
    )
    await pg_session.commit()

    fake_session = _FakeDiagramSession()

    async def fake_require_session(_ctx):
        return fake_session

    monkeypatch.setattr(tools, "_require_session", fake_require_session)
    workspace_id = chat_workspace_scope_id(chat_id)
    ctx = AgentContext(
        username=str(user_id),
        tenant_id=str(tenant_id),
        chat_id=chat_id,
        wf_id=workspace_id,
        turn_id=f"turn-{uuid.uuid4().hex}",
        runtime_session_id=f"runtime-{uuid.uuid4().hex}",
        vfs=PostgresVfsStore(),
    )
    fake_session.sync_callback = lambda path, data: ctx.vfs.upsert_artifact_bytes(
        wf_id=ctx.wf_id,
        path=path,
        data=data,
        content_type="application/vnd.vibecanvas.diagram+json",
    )
    runtime = SimpleNamespace(context=ctx)
    tenant_token = current_sync_tenant_id.set(str(tenant_id))
    try:
        spec_result = await _call(
            get_diagram_spec,
            family="flow",
            diagram_type="basic",
            schema_version=1,
            runtime=runtime,
        )
        draft_path = "/memory/diagram-drafts/request.vdiagram.json"
        draft = spec_result["minimal_example"]
        draft["id"] = "request-flow"
        draft["title"] = "Request flow"
        pinned_node_id = draft["model"]["nodes"][0]["id"]
        draft["view"]["overrides"] = {
            pinned_node_id: {
                "position": {"x": 72, "y": 72},
                "pinned": True,
                "owner": "user",
            }
        }
        raw = json.dumps(draft, ensure_ascii=False, sort_keys=True).encode()
        fake_session.files[draft_path] = raw
        source_ref = {
            "path": draft_path,
            "content_hash": tools._sha256(raw),
        }
        check_result = await _call(
            check_diagram,
            source_ref=source_ref,
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=None,
            runtime=runtime,
        )
        assert check_result["status"] == "ready"
        present_request = check_result["present_request"]
        Draft202012Validator(
            DIAGRAM_INPUT_SCHEMAS["present_diagram"]
        ).validate(present_request)
        first_present = await _call(
            present_diagram,
            **present_request,
            runtime=runtime,
        )
        first_ref = first_present["diagram_ref"]
        Draft202012Validator(
            DIAGRAM_INPUT_SCHEMAS["review_diagram"]
        ).validate(first_present["review_request"])
        assert first_present["delivery"]["event_id"] is not None
        async with tools.session_scope(tenant_id=str(tenant_id)) as session:
            active = await ChatRepo(session, str(user_id)).get_active_diagram(
                chat_id
            )
        assert active == {
            "diagram_ref": first_ref,
            "family": "flow",
            "type": "basic",
            "selected_element_ids": [],
            "viewport_bounds": None,
        }

        retry_present = await _call(
            present_diagram,
            **present_request,
            runtime=runtime,
        )
        assert retry_present["diagram_ref"] == first_ref
        assert retry_present["delivery"]["event_id"] == (
            first_present["delivery"]["event_id"]
        )
        assert await tools._current_diagram_ref(
            ctx,
            first_ref["path"],
        ) == first_ref

        canonical_check = await _call(
            check_diagram,
            source_ref={
                "path": first_ref["path"],
                "content_hash": tools._sha256(
                    fake_session.files[first_ref["path"]]
                ),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=first_ref,
            runtime=runtime,
        )
        assert canonical_check["status"] == "ready"
        assert canonical_check["diagram_ref"]["path"] == first_ref["path"]
        assert canonical_check["next"]["tool"] == "render_interactive"
        assert canonical_check["render_request"]["view"]["type"] == "file_preview"
        assert "present_request" not in canonical_check
        assert canonical_check["diagram_ref"] == first_ref
        assert first_ref["path"] not in fake_session.synced

        pin_violation = json.loads(raw)
        pin_violation["view"]["overrides"] = {}
        pin_violation_raw = json.dumps(
            pin_violation,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        fake_session.files[draft_path] = pin_violation_raw
        rejected_pin_check = await _call(
            check_diagram,
            source_ref={
                "path": draft_path,
                "content_hash": tools._sha256(pin_violation_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=first_ref,
            runtime=runtime,
        )
        assert rejected_pin_check["status"] == "invalid"
        assert "user_pin_overwritten" in {
            issue["code"] for issue in rejected_pin_check["issues"]
        }

        review = await _call(
            review_diagram,
            **first_present["review_request"],
            runtime=runtime,
        )
        assert review["status"] == "reviewed"
        assert review["image_delivery"]["image_count"] == 1
        assert review["image_delivery"]["mode"] == "on_demand_artifact_ref"
        assert review["image_delivery"]["delivered_to_model"] is False
        assert review["review_images"][0]["sandbox_path"] in fake_session.files
        assert review["review_context"]["visible_element_ids"]
        review_path = review["review_images"][0]["sandbox_path"]
        image_content, image_artifact = (
            await read_diagram_review_image.coroutine(
                sandbox_path=review_path,
                runtime=runtime,
            )
        )
        image_result = json.loads(image_content)
        assert image_result["status"] == "ok"
        assert image_result["sandbox_path"] == review_path
        image_block = image_artifact["meta"]["mcp_content"][0]
        assert image_block["type"] == "image"
        assert base64.b64decode(image_block["data"]) == (
            fake_session.files[review_path]
        )
        invalid_content, invalid_artifact = (
            await read_diagram_review_image.coroutine(
                sandbox_path="/memory/diagram-review-artifacts/../secret.png",
                runtime=runtime,
            )
        )
        assert invalid_artifact["status"] == "error"
        assert invalid_artifact["error"]["code"] == (
            "invalid_review_image_path"
        )
        assert "latest review_images" in invalid_content

        inspect = await _call(
            inspect_diagram,
            diagram_ref=first_ref,
            selector={"mode": "summary"},
            include=None,
            runtime=runtime,
        )
        assert all("bounds" in match for match in inspect["matches"])
        retained_layout = inspect["next"]["retained_layout"]
        assert retained_layout["layout_mode"] == "incremental"
        assert set(retained_layout["overrides"]) == {
            node["id"] for node in draft["model"]["nodes"]
        }
        assert retained_layout["overrides"][pinned_node_id] == (
            draft["view"]["overrides"][pinned_node_id]
        )

        accidental_fork_path = (
            "/memory/diagram-drafts/request-expanded.vdiagram.json"
        )
        accidental_fork = json.loads(raw)
        accidental_fork["view"]["layoutMode"] = retained_layout["layout_mode"]
        accidental_fork["view"]["overrides"] = retained_layout["overrides"]
        accidental_fork_raw = json.dumps(
            accidental_fork,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        fake_session.files[accidental_fork_path] = accidental_fork_raw
        rejected_fork_check = await _call(
            check_diagram,
            source_ref={
                "path": accidental_fork_path,
                "content_hash": tools._sha256(accidental_fork_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=None,
            runtime=runtime,
        )
        assert rejected_fork_check["status"] == "invalid"
        assert "modification_target_path_changed" in {
            issue["code"] for issue in rejected_fork_check["issues"]
        }

        modified = json.loads(raw)
        modified["title"] = "Updated request flow"
        modified["model"]["nodes"][1]["label"] = "Validate request"
        auto_modified_raw = json.dumps(
            modified,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        fake_session.files[draft_path] = auto_modified_raw
        rejected_auto_check = await _call(
            check_diagram,
            source_ref={
                "path": draft_path,
                "content_hash": tools._sha256(auto_modified_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=None,
            runtime=runtime,
        )
        assert rejected_auto_check["status"] == "invalid"
        assert "modify_layout_mode_auto" in {
            issue["code"] for issue in rejected_auto_check["issues"]
        }

        modified["view"]["layoutMode"] = retained_layout["layout_mode"]
        modified["view"]["overrides"] = retained_layout["overrides"]

        # A modification cannot silently drop or repurpose a base node. The
        # same semantic deletion becomes valid only when its exact stable ID is
        # explicitly declared to check_diagram.
        deletion = json.loads(json.dumps(modified))
        removed_id = deletion["model"]["nodes"][-1]["id"]
        deletion["model"]["nodes"] = [
            node for node in deletion["model"]["nodes"]
            if node["id"] != removed_id
        ]
        deletion["model"]["edges"] = [
            edge for edge in deletion["model"]["edges"]
            if removed_id not in {edge["source"], edge["target"]}
        ]
        deletion["intent"]["primaryPath"] = [
            node_id for node_id in deletion["intent"]["primaryPath"]
            if node_id != removed_id
        ]
        deletion["intent"]["constraints"] = []
        deletion["view"]["overrides"].pop(removed_id, None)
        deletion_raw = json.dumps(
            deletion,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        fake_session.files[draft_path] = deletion_raw
        undeclared_deletion = await _call(
            check_diagram,
            source_ref={
                "path": draft_path,
                "content_hash": tools._sha256(deletion_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=first_ref,
            runtime=runtime,
        )
        assert undeclared_deletion["status"] == "invalid"
        assert "unapproved_element_deletion" in {
            issue["code"] for issue in undeclared_deletion["issues"]
        }
        declared_deletion = await _call(
            check_diagram,
            source_ref={
                "path": draft_path,
                "content_hash": tools._sha256(deletion_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=first_ref,
            removed_element_ids=[removed_id],
            runtime=runtime,
        )
        assert declared_deletion["status"] == "ready", declared_deletion

        displaced = json.loads(json.dumps(modified))
        movable_id = next(
            node_id for node_id in retained_layout["overrides"]
            if node_id != pinned_node_id
        )
        displaced["view"]["overrides"][movable_id]["nudge"] = {
            "dx": 50,
            "dy": 0,
            "unit": "grid",
        }
        displaced_raw = json.dumps(
            displaced,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        fake_session.files[draft_path] = displaced_raw
        rejected_displacement = await _call(
            check_diagram,
            source_ref={
                "path": draft_path,
                "content_hash": tools._sha256(displaced_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            base_diagram_ref=first_ref,
            runtime=runtime,
        )
        assert rejected_displacement["status"] == "invalid"
        assert "mental_map_displacement_exceeded" in {
            issue["code"] for issue in rejected_displacement["issues"]
        }

        modified_raw = json.dumps(
            modified,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        fake_session.files[draft_path] = modified_raw
        modified_check = await _call(
            check_diagram,
            source_ref={
                "path": draft_path,
                "content_hash": tools._sha256(modified_raw),
            },
            spec_ref=spec_result["spec_ref"],
            validation_level="compile",
            # The service must safely recover when an Agent omitted the active
            # ref: it binds the current canonical revision into the signed
            # check instead of producing an unsafe create-style check.
            base_diagram_ref=None,
            runtime=runtime,
        )
        assert modified_check["status"] == "ready", modified_check
        assert "base_resolution" in modified_check, modified_check
        assert modified_check["base_resolution"] == "canonical"
        assert modified_check["present_request"][
            "expected_base_revision"
        ] == first_ref["revision"]
        modified_present = await _call(
            present_diagram,
            **modified_check["present_request"],
            runtime=runtime,
        )
        assert modified_present["diagram_ref"]["revision"] != (
            first_ref["revision"]
        )

        export = await _call(
            export_diagram,
            diagram_ref=modified_present["diagram_ref"],
            format="svg",
            focus={"mode": "canvas"},
            theme="light",
            scale=1.0,
            background="white",
            output_basename="request-flow",
            runtime=runtime,
        )
        cached_export = await _call(
            export_diagram,
            diagram_ref=modified_present["diagram_ref"],
            format="svg",
            focus={"mode": "canvas"},
            theme="light",
            scale=1.0,
            background="white",
            output_basename="request-flow",
            runtime=runtime,
        )
        assert export["export"]["cached"] is False
        assert cached_export["export"]["cached"] is True
        assert cached_export["download_ref"] == export["download_ref"]
    finally:
        current_sync_tenant_id.reset(tenant_token)

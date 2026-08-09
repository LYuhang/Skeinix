"""Agent-facing VibeDiagram lifecycle tools.

Content authoring intentionally remains in the existing filesystem tools. These
tools load the exact contract, validate an exact draft hash, promote one checked
source to durable VFS, inspect/review the presented revision, and export it.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from PIL import Image
from sqlalchemy import select

from vibecanvas_api.agents.tools._session_fs import _require_session
from vibecanvas_api.agents.tools.decorator import (
    ToolError,
    tool_error_boundary,
    tool_output,
)
from vibecanvas_api.config import config
from vibecanvas_api.diagrams.agent_contract import diagram_tool_description
from vibecanvas_api.diagrams.compiler import compile_diagram
from vibecanvas_api.diagrams.isolated_render import (
    render_scene_pdf_isolated,
    render_scene_png_isolated,
    render_scene_svg_isolated,
)
from vibecanvas_api.diagrams.limits import (
    MAX_CANVAS_EXTENT,
    MAX_JSON_DEPTH,
    MAX_PDF_BYTES,
    MAX_PNG_BYTES,
    MAX_REVIEW_HEIGHT,
    MAX_REVIEW_IMAGES,
    MAX_REVIEW_PIXELS,
    MAX_REVIEW_WIDTH,
    MAX_SOURCE_BYTES,
    MAX_SVG_BYTES,
    DiagramLimitError,
)
from vibecanvas_api.diagrams.models import (
    DiagramDocument,
    DiagramScene,
    SceneBounds,
)
from vibecanvas_api.diagrams.registry import (
    ALLOWED_CONSTRAINTS,
    ASSET_CATALOG,
    BASE_AUTHORING_INSTRUCTIONS,
    COMPILER_VERSION,
    FORBIDDEN_PATTERNS,
    REGISTRY_VERSION,
    THEME_VERSION,
    base_schema_hash,
    get_diagram_type,
)
from vibecanvas_api.diagrams.render_validation import validate_diagram_for_render
from vibecanvas_api.diagrams.validator import parse_and_validate
from vibecanvas_api.services.file_revision import vfs_row_revision
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.diagram_draft_repo import DiagramDraftRepo
from vibecanvas_api.storage.models import VfsArtifact, VfsArtifactEvent
from vibecanvas_api.storage.vfs_run_repo import VfsRunRepo
from vibecanvas_api.storage.vfs_store import VfsRepo

_CHECK_DOMAIN = b"vibecanvas:diagram-check:v1\0"
_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_REVIEW_IMAGE_PATH = re.compile(
    r"^/memory/diagram-review-artifacts/review_[a-f0-9]{16}\.png$"
)


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _constraint_node_ids(constraint: Any) -> set[str]:
    """Return node identifiers referenced by every supported constraint kind."""
    identifiers = set(getattr(constraint, "elements", ()) or ())
    for field in ("element", "target", "container"):
        value = getattr(constraint, field, None)
        if isinstance(value, str) and value:
            identifiers.add(value)
    return identifiers


def _retained_layout_seed(
    document: DiagramDocument,
    scene: DiagramScene,
) -> dict[str, Any]:
    """Return source-ready positions for a deterministic incremental edit.

    Compiler positions are durable view state, not Agent-guessed geometry.
    User-owned pinned overrides remain semantically exact so an incremental
    edit cannot silently take ownership of them.
    """
    scene_by_id = {node.id: node for node in scene.nodes}
    overrides: dict[str, Any] = {}
    for node in document.model.nodes:
        existing = document.view.overrides.get(node.id)
        if existing is not None and existing.owner == "user" and existing.pinned:
            overrides[node.id] = existing.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            continue
        bounds = scene_by_id[node.id].bounds
        overrides[node.id] = {
            "position": {"x": bounds.x, "y": bounds.y},
            "owner": "compiler",
        }
    return {
        "layout_mode": (
            "preserve"
            if document.view.layout_mode == "preserve"
            else "incremental"
        ),
        "overrides": overrides,
        "instruction": (
            "Copy layout_mode to view.layoutMode and copy overrides for every "
            "retained element. Keep user-owned entries exact. Remove an entry "
            "only when deleting that element; new elements need no override "
            "in incremental mode."
        ),
    }


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_check(claims: dict[str, Any]) -> str:
    body = _b64(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    )
    signature = _b64(
        hmac.new(
            config.signing_secret.encode(),
            _CHECK_DOMAIN + body.encode(),
            hashlib.sha256,
        ).digest()
    )
    return f"dcheck_{body}.{signature}"


def _verify_check(value: str, ctx) -> dict[str, Any]:
    try:
        raw = value.removeprefix("dcheck_")
        body, signature = raw.split(".", 1)
        expected = _b64(
            hmac.new(
                config.signing_secret.encode(),
                _CHECK_DOMAIN + body.encode(),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        claims = json.loads(_unb64(body))
    except Exception as exc:
        raise ToolError(
            "invalid_check_ref",
            "The check reference is invalid; run check_diagram again.",
        ) from exc
    if int(claims.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
        raise ToolError(
            "check_expired",
            "The check reference expired; run check_diagram again.",
        )
    bindings = {
        "tenant": str(getattr(ctx, "tenant_id", "")),
        "user": str(getattr(ctx, "username", "")),
        "chat": str(getattr(ctx, "chat_id", "")),
        "workspace": str(getattr(ctx, "wf_id", "")),
        "turn": str(getattr(ctx, "turn_id", "")),
        "runtime_session": str(getattr(ctx, "runtime_session_id", "")),
    }
    if any(claims.get(key) != value for key, value in bindings.items()):
        raise ToolError(
            "check_scope_mismatch",
            "The check reference belongs to another workspace.",
        )
    return claims


def _authoring_schema(spec) -> dict[str, Any]:
    schema = copy.deepcopy(DiagramDocument.model_json_schema(by_alias=True))
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["description"] = f"Complete VibeDiagram contract for {spec.key}."
    definitions = schema.get("$defs", {})
    node_kind = (
        definitions.get("SemanticNode", {}).get("properties", {}).get("kind")
    )
    edge_kind = (
        definitions.get("SemanticEdge", {}).get("properties", {}).get("kind")
    )
    if isinstance(node_kind, dict):
        node_kind["enum"] = list(spec.allowed_node_kinds)
    if isinstance(edge_kind, dict):
        edge_kind["enum"] = list(spec.allowed_edge_kinds)
    return schema


def _minimal_example(spec) -> dict[str, Any]:
    architecture = spec.family == "architecture"
    nodes = [
        {
            "id": "user",
            "kind": "actor" if architecture else "start",
            "label": "User" if architecture else "Start",
            "styleRole": "actor" if architecture else "primary",
        },
        {
            "id": "service",
            "kind": "service" if architecture else "process",
            "label": "Application service" if architecture else "Process request",
            "styleRole": "service" if architecture else "neutral",
        },
        {
            "id": "result",
            "kind": "database" if architecture else "end",
            "label": "Data store" if architecture else "Done",
            "styleRole": "storage" if architecture else "success",
        },
    ]
    edge_kind = "request" if architecture else "flow"
    return {
        "schemaVersion": 1,
        "id": "example-diagram",
        "title": "Example diagram",
        "diagram": {"family": spec.family, "type": spec.type},
        "model": {
            "nodes": nodes,
            "edges": [
                {
                    "id": "user-service",
                    "source": "user",
                    "target": "service",
                    "kind": edge_kind,
                    "label": "Request" if architecture else "Next",
                },
                {
                    "id": "service-result",
                    "source": "service",
                    "target": "result",
                    "kind": "data-flow" if architecture else "flow",
                    "label": "Stores" if architecture else "Complete",
                },
            ],
            "groups": [],
            "embeds": [],
            "resources": [],
        },
        "intent": {
            "direction": "RIGHT",
            "density": "comfortable",
            "stability": "preserve",
            "primaryPath": ["user", "service", "result"],
            "constraints": [],
        },
        "view": {"layoutMode": "auto", "overrides": {}, "frames": []},
        "metadata": {
            "createdBy": "agent",
            "specVersion": REGISTRY_VERSION,
            "specHash": spec.spec_hash,
            "compilerVersion": None,
            "themeVersion": None,
        },
    }


@tool_output(
    content_type="application/json",
    tool="get_diagram_spec",
    inline_chars=64_000,
)
async def _do_get_diagram_spec(
    family: str,
    diagram_type: str,
    schema_version: int,
    runtime: ToolRuntime,
) -> dict:
    if schema_version != 1:
        raise ToolError(
            "unsupported_schema_version",
            "Only VibeDiagram schema version 1 is enabled.",
        )
    spec = get_diagram_type(family, diagram_type)
    if spec is None:
        raise ToolError(
            "diagram_type_not_enabled",
            f"Diagram type '{family}/{diagram_type}' is not enabled.",
        )
    return {
        "status": "ok",
        "spec_ref": {
            "schema_version": 1,
            "family": spec.family,
            "type": spec.type,
            "spec_version": REGISTRY_VERSION,
            "spec_hash": spec.spec_hash,
        },
        "selection_rationale": {
            "use_when": spec.use_when,
            "do_not_use_when": list(spec.do_not_use_when),
            "semantic_focus": list(spec.semantic_focus),
        },
        "document_contract": {
            "root_required": [
                "schemaVersion", "id", "title", "diagram", "model",
                "intent", "view", "metadata",
            ],
            "base_schema_version": 1,
            "base_schema_hash": base_schema_hash(),
            "schema_resource": "vibecanvas://diagram/schema/v1",
            "max_source_bytes": MAX_SOURCE_BYTES,
            "max_json_depth": MAX_JSON_DEPTH,
            "max_nodes": 500,
            "max_edges": 1000,
            "max_groups": 100,
            "max_frames": 32,
            "max_canvas_extent": MAX_CANVAS_EXTENT,
            "max_review_images": MAX_REVIEW_IMAGES,
            "max_review_width": MAX_REVIEW_WIDTH,
            "max_review_height": MAX_REVIEW_HEIGHT,
            "max_review_pixels": MAX_REVIEW_PIXELS,
            "max_export_bytes": {
                "svg": MAX_SVG_BYTES,
                "png": MAX_PNG_BYTES,
                "pdf": MAX_PDF_BYTES,
            },
        },
        "authoring_schema": _authoring_schema(spec),
        "authoring_instructions": list(BASE_AUTHORING_INSTRUCTIONS),
        "forbidden_patterns": list(FORBIDDEN_PATTERNS),
        "recommended_layout": {
            "compiler": "graph",
            "direction": "RIGHT",
            "density": "comfortable",
            "initial_mode": "auto",
            "modify_mode": "incremental",
        },
        "allowed_node_kinds": list(spec.allowed_node_kinds),
        "allowed_edge_kinds": list(spec.allowed_edge_kinds),
        "allowed_constraints": list(ALLOWED_CONSTRAINTS),
        "required_semantics": list(spec.required_semantics),
        "quality_rules": [
            "Keep one visible primary path where the diagram has a main flow.",
            "Do not encode required meaning only with color.",
        ],
        "quality_policy": dict(spec.quality_policy),
        "asset_policy": {
            "search_required_for": [
                "branded-cloud-service",
                "vendor-network-device",
            ],
            "default_asset_catalog": "platform",
        },
        "minimal_example": _minimal_example(spec),
        "next": {
            "action": "edit_source",
            "reason": "Author the complete semantic source in the auto-saved Diagram folder.",
            "write_path_pattern": (
                "/data/diagrams/<slug>.vdiagram.json"
            ),
            "then": {
                "tool": "check_diagram",
                "requires": ["source_ref", "spec_ref"],
            },
        },
    }


@tool(response_format="content_and_artifact")
async def get_diagram_spec(
    family: str,
    diagram_type: str,
    schema_version: int = 1,
    *,
    runtime: ToolRuntime,
):
    """Load the complete authoring contract for one exact enabled diagram type.

    Choose family/type from the /diagram command catalog. The returned spec_ref,
    schema, instructions and complete minimal_example are the source of truth.
    Pass spec_ref unchanged to check_diagram.
    """
    return await _do_get_diagram_spec(family, diagram_type, schema_version, runtime)


@tool_output(content_type="application/json", tool="search_diagram_assets")
async def _do_search_assets(
    query: str,
    family: str,
    diagram_type: str,
    asset_kinds: list[str],
    limit: int,
    runtime: ToolRuntime,
) -> dict:
    if get_diagram_type(family, diagram_type) is None:
        raise ToolError(
            "diagram_type_not_enabled",
            "The selected diagram type is not enabled.",
        )
    catalog = [
        {
            "asset_key": asset_key,
            **{
                key: list(value) if isinstance(value, tuple) else value
                for key, value in asset.items()
            },
            "source": "platform-bundled",
        }
        for asset_key, asset in sorted(ASSET_CATALOG.items())
    ]
    words = query.lower().split()
    matches = [
        item
        for item in catalog
        if not words
        or any(
            word in (item["title"] + " " + item["asset_key"]).lower()
            for word in words
        )
    ]
    if asset_kinds:
        matches = [item for item in matches if item["asset_kind"] in asset_kinds]
    return {
        "status": "ok",
        "catalog_version": REGISTRY_VERSION,
        "assets": matches[: max(1, min(limit, 20))],
        "next": {
            "action": "edit_source",
            "reason": (
                "Copy a returned asset_key into a compatible node assetRef."
                if matches
                else "Use a spec-allowed semantic fallback without assetRef."
            ),
        },
    }


@tool(response_format="content_and_artifact")
async def search_diagram_assets(
    query: str,
    family: str,
    diagram_type: str,
    asset_kinds: list[str] | None = None,
    limit: int = 10,
    *,
    runtime: ToolRuntime,
):
    """Search the local platform diagram asset catalog; never fetches external URLs."""
    return await _do_search_assets(
        query, family, diagram_type, asset_kinds or [], limit, runtime
    )


async def _sandbox_source(ctx, source_ref: dict[str, Any]) -> tuple[str, bytes, str]:
    path = str(source_ref.get("path") or "")
    if (
        not (
            path.startswith("/memory/diagram-drafts/")
            or path.startswith("/data/diagrams/")
        )
        or not path.endswith(".vdiagram.json")
        or ".." in path.split("/")
    ):
        raise ToolError(
            "invalid_diagram_path",
            "Diagram sources must be .vdiagram.json files under "
            "/data/diagrams (or the legacy /memory/diagram-drafts path).",
        )
    session = await _require_session(ctx)
    result = await session.read_file(path)
    if not result.get("ok") or result.get("kind") == "binary":
        raise ToolError("draft_not_found", f"Unable to read diagram draft {path}.")
    raw = str(result.get("content") or "").encode()
    actual_hash = _sha256(raw)
    expected_hash = str(source_ref.get("content_hash") or "")
    if actual_hash != expected_hash:
        raise ToolError(
            "stale_source",
            f"Draft content changed: expected {expected_hash}, "
            f"actual {actual_hash}. Re-read and check again.",
        )
    return path, raw, actual_hash


async def _do_check_canonical_diagram(
    *,
    ctx,
    source_ref: dict[str, Any],
    spec_ref: dict[str, Any],
    validation_level: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """Validate the auto-saved canonical file without a publish transaction."""
    path, raw, actual_hash = await _sandbox_source(ctx, source_ref)
    spec = get_diagram_type(
        str(spec_ref.get("family") or ""),
        str(spec_ref.get("type") or ""),
    )
    if (
        spec is None
        or spec_ref.get("spec_hash") != spec.spec_hash
        or spec_ref.get("spec_version") != REGISTRY_VERSION
    ):
        raise ToolError(
            "stale_spec",
            "The diagram spec is unknown or stale; call get_diagram_spec again.",
        )
    if validation_level not in {"semantic", "compile"}:
        raise ToolError(
            "invalid_validation_level",
            "validation_level must be semantic or compile.",
        )

    validation = validate_diagram_for_render(raw)
    issues = list(validation.issues)
    document = validation.document
    if document is not None and (
        document.diagram.family != spec.family
        or document.diagram.type != spec.type
    ):
        issues.append({
            "severity": "error",
            "disposition": "blocking",
            "stage": "semantic",
            "code": "spec_type_mismatch",
            "json_pointer": "/diagram",
            "message": "Source diagram type differs from spec_ref.",
            "suggested_fix": "Use the exact family/type returned by get_diagram_spec.",
        })
    blocking = [
        issue
        for issue in issues
        if issue.get("severity") == "error"
        or issue.get("disposition") == "blocking"
    ]
    if document is None or blocking:
        return {
            "status": "invalid",
            "source_ref": source_ref,
            "spec_ref": spec_ref,
            "presentable": False,
            "issues": blocking or issues,
            "warnings": [issue for issue in issues if issue not in blocking],
            "next": {
                "action": "edit_source",
                "reason": "Repair the reported source pointers, then run check_diagram again.",
                "repair_issue_indexes": list(range(len(blocking or issues))),
            },
        }
    if validation_level == "semantic":
        return {
            "status": "valid",
            "source_ref": source_ref,
            "spec_ref": spec_ref,
            "presentable": False,
            "issues": [],
            "warnings": issues,
            "next": {
                "action": "call_tool",
                "tool": "check_diagram",
                "request": {
                    "source_ref": source_ref,
                    "spec_ref": spec_ref,
                    "validation_level": "compile",
                },
            },
        }

    assert validation.scene is not None
    sandbox_session = await _require_session(ctx)
    async with session_scope(tenant_id=str(ctx.tenant_id)) as read_session:
        durable_raw = await VfsRepo(
            read_session,
            object_store=get_object_store(),
        ).read_bytes(wf_id=str(ctx.wf_id), path=path)
    if durable_raw != raw:
        sync_path = getattr(sandbox_session, "sync_workspace_path", None)
        if not callable(sync_path) or not await sync_path(path):
            raise ToolError(
                "diagram_vfs_sync_failed",
                "The validated Diagram file could not be saved to VFS. Keep the file and retry check_diagram.",
            )
    scene_json = validation.scene.model_dump_json(
        by_alias=True,
        exclude_none=True,
    ).encode()
    scene_ref = f"scene://{_sha256(scene_json)}"
    async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
        row = (
            await session.execute(
                select(VfsArtifact).where(
                    VfsArtifact.scope_id == str(ctx.wf_id),
                    VfsArtifact.path == path,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ToolError(
                "diagram_vfs_sync_failed",
                "The validated Diagram file is not yet durable. Retry check_diagram.",
            )
        diagram_ref = _diagram_ref(
            path,
            vfs_row_revision(row),
            actual_hash,
            scene_ref,
        )
        await ChatRepo(session, str(ctx.username)).set_active_diagram(
            str(ctx.chat_id),
            diagram_ref,
            family=document.diagram.family,
            diagram_type=document.diagram.type,
        )
    render_request = {
        "title": document.title,
        "view": {
            "type": "file_preview",
            "path": path,
            "mime": "application/vnd.vibecanvas.diagram+json",
            "description": "Validated Diagram",
        },
    }
    warnings = [
        issue
        for issue in issues
        if issue.get("severity") != "error"
        and issue.get("disposition") != "blocking"
    ]
    return {
        "status": "ready",
        "source_ref": source_ref,
        "spec_ref": spec_ref,
        "presentable": True,
        "diagram_ref": diagram_ref,
        "preview_ref": {
            "schemaVersion": 1,
            "kind": "file",
            "fileRef": {
                "schemaVersion": 1,
                "scope": "chat",
                "chatId": str(ctx.chat_id),
                "path": path,
            },
        },
        "checked_source_hash": actual_hash,
        "checked_bundle_hash": actual_hash,
        "scene_ref": scene_ref,
        "compiler_version": COMPILER_VERSION,
        "theme_version": THEME_VERSION,
        "issues": [],
        "warnings": warnings,
        "summary": {
            "family": document.diagram.family,
            "type": document.diagram.type,
            "nodes": len(document.model.nodes),
            "edges": len(document.model.edges),
            "frames": len(document.view.frames),
        },
        "auto_repair": validation.scene.auto_repair.model_dump(
            mode="json", by_alias=True
        ),
        "next": {
            "action": "call_tool",
            "tool": "render_interactive",
            "reason": "The current file is compile-ready; render_interactive performs the final safety check before showing a card.",
            "request": render_request,
        },
        "render_request": render_request,
    }


async def _active_diagram_ref(ctx) -> dict[str, Any] | None:
    """Return the current Chat-owned exact DiagramRef, including legacy shape."""
    async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
        active = await ChatRepo(session, str(ctx.username)).get_active_diagram(
            str(ctx.chat_id)
        )
    if not isinstance(active, dict):
        return None
    nested = active.get("diagram_ref")
    candidate = nested if isinstance(nested, dict) else active
    required = {"path", "revision", "source_hash", "scene_ref"}
    return dict(candidate) if required.issubset(candidate) else None


async def _begin_draft_revision(
    ctx,
    *,
    source_path: str,
    target_path: str,
    source_hash: str,
) -> tuple[str, int]:
    async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
        cursor = await DiagramDraftRepo(session).begin_source(
            tenant_id=str(ctx.tenant_id),
            owner_user_id=str(ctx.username),
            chat_id=str(ctx.chat_id),
            turn_id=str(ctx.turn_id),
            workspace_scope_id=str(ctx.wf_id),
            source_path=source_path,
            target_path=target_path,
            source_hash=source_hash,
        )
    return cursor.draft_id, cursor.sequence


async def _set_draft_revision_status(
    ctx,
    draft_id: str,
    sequence: int,
    status: Literal["compiling", "invalid"],
) -> None:
    async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
        repo = DiagramDraftRepo(session)
        if status == "compiling":
            await repo.mark_compiling(draft_id, sequence)
        else:
            await repo.mark_invalid(draft_id, sequence)


def _draft_operation(
    scene: DiagramScene,
    base_scene: DiagramScene | None,
) -> tuple[str, list[str]]:
    current = {
        "node": {item.id: item for item in scene.nodes},
        "edge": {item.id: item for item in scene.edges},
        "group": {item.id: item for item in scene.groups},
    }
    if base_scene is None:
        return "create_diagram", sorted(
            item_id for items in current.values() for item_id in items
        )
    previous = {
        "node": {item.id: item for item in base_scene.nodes},
        "edge": {item.id: item for item in base_scene.edges},
        "group": {item.id: item for item in base_scene.groups},
    }
    changes: list[tuple[str, str, str]] = []
    for kind in ("node", "edge", "group"):
        current_ids = set(current[kind])
        previous_ids = set(previous[kind])
        changes.extend(("add", kind, item_id) for item_id in current_ids - previous_ids)
        changes.extend(("remove", kind, item_id) for item_id in previous_ids - current_ids)
        for item_id in current_ids & previous_ids:
            before = previous[kind][item_id].model_dump(mode="json", by_alias=True)
            after = current[kind][item_id].model_dump(mode="json", by_alias=True)
            if before != after:
                changes.append(("update", kind, item_id))
    if not changes:
        return "compile_revision", []
    verbs = {(verb, kind) for verb, kind, _ in changes}
    operation = (
        f"{next(iter(verbs))[0]}_{next(iter(verbs))[1]}"
        if len(verbs) == 1
        else "update_diagram"
    )
    return operation, sorted({item_id for _, _, item_id in changes})


def _draft_preview_ref(
    ctx,
    *,
    draft_id: str,
    target_path: str,
    title: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "diagram_draft",
        "draftId": draft_id,
        "chatId": str(ctx.chat_id),
        "targetPath": target_path,
        "title": title,
    }


@tool_output(content_type="application/json", tool="check_diagram")
async def _do_check_diagram(
    source_ref: dict[str, Any],
    spec_ref: dict[str, Any],
    validation_level: str,
    base_diagram_ref: dict[str, Any] | None,
    runtime: ToolRuntime,
    removed_element_ids: list[str] | None = None,
    finalize: bool = True,
) -> dict:
    ctx = runtime.context
    if str(source_ref.get("path") or "").startswith("/data/diagrams/"):
        return await _do_check_canonical_diagram(
            ctx=ctx,
            source_ref=source_ref,
            spec_ref=spec_ref,
            validation_level=validation_level,
            runtime=runtime,
        )
    path, raw, actual_hash = await _sandbox_source(ctx, source_ref)
    target = f"/data/diagrams/{os.path.basename(path)}"
    spec = get_diagram_type(
        str(spec_ref.get("family") or ""),
        str(spec_ref.get("type") or ""),
    )
    if (
        spec is None
        or spec_ref.get("spec_hash") != spec.spec_hash
        or spec_ref.get("spec_version") != REGISTRY_VERSION
    ):
        raise ToolError(
            "stale_spec",
            "The diagram spec is unknown or stale; call get_diagram_spec again.",
        )
    try:
        draft_id, draft_sequence = await _begin_draft_revision(
            ctx,
            source_path=path,
            target_path=target,
            source_hash=actual_hash,
        )
    except ValueError as exc:
        raise ToolError(
            "stale_diagram_draft",
            "This Diagram draft Turn is already complete; start the next edit "
            "from the active Diagram context.",
        ) from exc
    # A model can omit base_diagram_ref even after inspecting an active
    # diagram. Bind the check to the current canonical revision server-side in
    # that case. The signed check still feeds compare-and-swap at presentation,
    # so this is fail-safe under concurrent updates rather than an overwrite
    # fallback. An explicitly supplied stale ref continues to fail closed in
    # _load_presented.
    effective_base_ref = base_diagram_ref
    base_resolution = "provided" if base_diagram_ref is not None else "none"
    if effective_base_ref is None:
        effective_base_ref = await _current_diagram_ref(ctx, target)
        if effective_base_ref is not None:
            base_resolution = "canonical"
    document, issues = parse_and_validate(raw)
    # A repair/modify Turn can omit the inspected base and rename its draft.
    # If the semantic diagram id still matches the active diagram, that is an
    # accidental fork rather than a new create. Bind the active ref so the
    # path mismatch is rejected as a source-addressable error. An intentional
    # copy remains possible as a separate create with a new document id.
    if effective_base_ref is None and document is not None:
        active_ref = await _active_diagram_ref(ctx)
        if active_ref is not None and active_ref.get("path") != target:
            active_document, _, _ = await _load_presented(ctx, active_ref)
            if active_document.id == document.id:
                effective_base_ref = active_ref
                base_resolution = "active_diagram"
    base_document = None
    base_scene = None
    if effective_base_ref is not None:
        base_document, base_scene, _ = await _load_presented(
            ctx,
            effective_base_ref,
        )
    if effective_base_ref is not None and effective_base_ref.get("path") != target:
        issues.append({
            "severity": "error",
            "stage": "semantic",
            "code": "modification_target_path_changed",
            "json_pointer": "/id",
            "message": (
                "A modification must keep the inspected canonical diagram path."
            ),
            "suggested_fix": (
                "Write the edited source to /memory/diagram-drafts/"
                f"{os.path.basename(str(effective_base_ref.get('path') or ''))} "
                "and pass the inspected DiagramRef as base_diagram_ref. To "
                "create an intentional copy instead, use a new diagram id."
            ),
        })
    if document and (document.diagram.family != spec.family or document.diagram.type != spec.type):
        issues.append({
            "severity": "error",
            "stage": "semantic",
            "code": "spec_type_mismatch",
            "json_pointer": "/diagram",
            "message": "Source diagram type differs from spec_ref.",
        })
    if document and base_document and (
        document.diagram.family != base_document.diagram.family
        or document.diagram.type != base_document.diagram.type
    ):
        issues.append({
            "severity": "error",
            "stage": "semantic",
            "code": "base_diagram_type_mismatch",
            "json_pointer": "/diagram",
            "message": "Modified source must keep the presented diagram family/type.",
        })
    if document and base_document:
        declared_removals = {
            str(node_id)
            for node_id in (removed_element_ids or [])
            if str(node_id)
        }
        base_node_ids = {node.id for node in base_document.model.nodes}
        draft_node_ids = {node.id for node in document.model.nodes}
        unknown_removals = sorted(declared_removals - base_node_ids)
        for node_id in unknown_removals:
            issues.append({
                "severity": "error",
                "stage": "semantic",
                "code": "unknown_removed_element_id",
                "json_pointer": "/model/nodes",
                "element_id": node_id,
                "message": (
                    "removed_element_ids contains an ID absent from the "
                    "inspected base revision."
                ),
                "suggested_fix": (
                    "Copy only exact stable IDs from inspect_diagram and list "
                    "one only when the user explicitly requested its removal."
                ),
            })
        unapproved_removals = sorted(
            (base_node_ids - draft_node_ids) - declared_removals
        )
        for node_id in unapproved_removals:
            issues.append({
                "severity": "error",
                "stage": "semantic",
                "code": "unapproved_element_deletion",
                "json_pointer": "/model/nodes",
                "element_id": node_id,
                "message": (
                    "A base element disappeared without an explicit deletion "
                    "declaration, so the modification may have repurposed or "
                    "dropped unrelated user content."
                ),
                "suggested_fix": (
                    "Restore this exact stable ID. If and only if the user "
                    "explicitly requested its deletion or replacement, rerun "
                    "check_diagram with this ID in removed_element_ids."
                ),
            })
        if document.view.layout_mode == "auto":
            issues.append({
                "severity": "error",
                "stage": "semantic",
                "code": "modify_layout_mode_auto",
                "json_pointer": "/view/layoutMode",
                "message": (
                    "A modification cannot use auto layout because it would "
                    "reflow retained elements and break the user's mental map."
                ),
                "suggested_fix": (
                    "Copy inspect_diagram.next.retained_layout.layout_mode and "
                    "overrides into the draft, then check again."
                ),
            })
        elif base_scene is not None:
            base_bounds = {node.id: node.bounds for node in base_scene.nodes}
            retained_ids = sorted(
                set(base_bounds)
                & {node.id for node in document.model.nodes}
            )
            for node_id in retained_ids:
                override = document.view.overrides.get(node_id)
                if override is None or override.position is None:
                    escaped_id = node_id.replace("~", "~0").replace("/", "~1")
                    issues.append({
                        "severity": "error",
                        "stage": "semantic",
                        "code": "incremental_position_missing",
                        "json_pointer": f"/view/overrides/{escaped_id}",
                        "element_id": node_id,
                        "message": (
                            "A retained element has no compiler/user-owned "
                            "position from the inspected base revision."
                        ),
                        "suggested_fix": (
                            "Copy this element's exact entry from "
                            "inspect_diagram.next.retained_layout.overrides."
                        ),
                    })
                    continue
                expected = base_bounds[node_id]
                if (
                    abs(override.position.x - expected.x) > 0.01
                    or abs(override.position.y - expected.y) > 0.01
                ):
                    escaped_id = node_id.replace("~", "~0").replace("/", "~1")
                    issues.append({
                        "severity": "error",
                        "stage": "semantic",
                        "code": "incremental_base_position_changed",
                        "json_pointer": (
                            f"/view/overrides/{escaped_id}/position"
                        ),
                        "element_id": node_id,
                        "message": (
                            "A retained element's base position differs from "
                            "the inspected canonical scene."
                        ),
                        "suggested_fix": (
                            "Restore the exact retained position from "
                            "inspect_diagram.next.retained_layout; use a grid "
                            "nudge only for a deliberate bounded visual repair."
                        ),
                    })
        for node_id, old_override in base_document.view.overrides.items():
            if old_override.owner != "user" or not old_override.pinned:
                continue
            new_override = document.view.overrides.get(node_id)
            if (
                new_override is None
                or new_override.owner != "user"
                or not new_override.pinned
                or new_override.position != old_override.position
                or new_override.nudge != old_override.nudge
                or new_override.width != old_override.width
                or new_override.height != old_override.height
            ):
                escaped_id = node_id.replace("~", "~0").replace("/", "~1")
                issues.append({
                    "severity": "error",
                    "stage": "semantic",
                    "code": "user_pin_overwritten",
                    "json_pointer": f"/view/overrides/{escaped_id}",
                    "element_id": node_id,
                    "message": (
                        "A user-owned pinned override was changed or removed."
                    ),
                    "suggested_fix": (
                        "Restore the exact user-owned position and size from "
                        "the inspected base revision."
                    ),
                })
    serialized_issues = [
        issue.model_dump(mode="json", by_alias=True)
        if hasattr(issue, "model_dump")
        else issue
        for issue in issues
    ]
    if document is None or any(item["severity"] == "error" for item in serialized_issues):
        await _set_draft_revision_status(
            ctx, draft_id, draft_sequence, "invalid"
        )
        return {
            "status": "invalid",
            "source_ref": source_ref,
            "spec_ref": spec_ref,
            "presentable": False,
            "issues": serialized_issues,
            "next": {
                "action": "edit_source",
                "reason": "The draft has schema or semantic errors.",
                "repair_issue_indexes": list(range(len(serialized_issues))),
                "then": {"tool": "check_diagram", "reuse": ["spec_ref"]},
            },
        }
    if validation_level not in {"semantic", "compile"}:
        raise ToolError(
            "invalid_validation_level",
            "validation_level must be semantic or compile.",
        )
    if validation_level == "semantic":
        return {
            "status": "valid",
            "source_ref": source_ref,
            "spec_ref": spec_ref,
            "presentable": False,
            "issues": serialized_issues,
            "next": {
                "action": "call_tool",
                "tool": "check_diagram",
                "request": {
                    "source_ref": source_ref,
                    "spec_ref": spec_ref,
                    "validation_level": "compile",
                    "base_diagram_ref": base_diagram_ref,
                    "removed_element_ids": removed_element_ids or [],
                },
            },
        }
    await _set_draft_revision_status(
        ctx, draft_id, draft_sequence, "compiling"
    )
    try:
        scene = compile_diagram(document)
    except DiagramLimitError as exc:
        limit_issue = {
            "severity": "error",
            "stage": "compile",
            "code": exc.code,
            "json_pointer": "/view",
            "message": str(exc),
            "suggested_fix": (
                "Reduce diagram extent or complexity, then run check_diagram "
                "again."
            ),
        }
        await _set_draft_revision_status(
            ctx, draft_id, draft_sequence, "invalid"
        )
        return {
            "status": "invalid",
            "source_ref": source_ref,
            "spec_ref": spec_ref,
            "presentable": False,
            "issues": [*serialized_issues, limit_issue],
            "next": {
                "action": "edit_source",
                "reason": "The draft exceeds a compiler resource limit.",
                "repair_issue_indexes": [len(serialized_issues)],
                "then": {"tool": "check_diagram", "reuse": ["spec_ref"]},
            },
        }
    if base_scene is not None:
        base_bounds_by_id = {node.id: node.bounds for node in base_scene.nodes}
        scene_bounds_by_id = {node.id: node.bounds for node in scene.nodes}
        x_limit = max(320.0, base_scene.bounds.width * 0.45)
        y_limit = max(180.0, base_scene.bounds.height * 0.45)
        displacement_issues: list[dict[str, Any]] = []
        for node_id in sorted(set(base_bounds_by_id) & set(scene_bounds_by_id)):
            before = base_bounds_by_id[node_id]
            after = scene_bounds_by_id[node_id]
            dx = abs(after.x - before.x)
            dy = abs(after.y - before.y)
            if dx <= x_limit and dy <= y_limit:
                continue
            escaped_id = node_id.replace("~", "~0").replace("/", "~1")
            displacement_issues.append({
                "severity": "error",
                "stage": "visual",
                "code": "mental_map_displacement_exceeded",
                "json_pointer": f"/view/overrides/{escaped_id}",
                "element_id": node_id,
                "message": (
                    "The compiled position of a retained element moved beyond "
                    "the bounded mental-map allowance."
                ),
                "suggested_fix": (
                    "Restore the inspect_diagram retained position and remove "
                    "large nudges or conflicting constraints. Place only new "
                    "elements around the retained layout."
                ),
                "details": {
                    "dx": round(dx, 2),
                    "dy": round(dy, 2),
                    "max_dx": round(x_limit, 2),
                    "max_dy": round(y_limit, 2),
                },
            })
        if displacement_issues:
            await _set_draft_revision_status(
                ctx, draft_id, draft_sequence, "invalid"
            )
            return {
                "status": "invalid",
                "source_ref": source_ref,
                "spec_ref": spec_ref,
                "presentable": False,
                "issues": displacement_issues,
                "next": {
                    "action": "edit_source",
                    "reason": (
                        "The compiled modification would break the retained "
                        "mental map."
                    ),
                    "repair_issue_indexes": list(
                        range(len(displacement_issues))
                    ),
                    "then": {"tool": "check_diagram", "reuse": ["spec_ref"]},
                },
            }
    warnings = [item.model_dump(mode="json", by_alias=True) for item in scene.issues]
    blocking_issues = [
        item for item in warnings if item.get("disposition") == "blocking"
    ]
    if blocking_issues:
        await _set_draft_revision_status(
            ctx, draft_id, draft_sequence, "invalid"
        )
        return {
            "status": "invalid",
            "source_ref": source_ref,
            "spec_ref": spec_ref,
            "presentable": False,
            "issues": blocking_issues,
            "auto_repair": scene.auto_repair.model_dump(
                mode="json", by_alias=True
            ),
            "next": {
                "action": "edit_source",
                "reason": (
                    "The compiler found blocking visual issues after bounded "
                    "automatic repair."
                ),
                "repair_issue_ids": [
                    item.get("issue_id") for item in blocking_issues
                ],
                "then": {"tool": "check_diagram", "reuse": ["spec_ref"]},
            },
        }
    scene_json = scene.model_dump_json(by_alias=True, exclude_none=True).encode()
    scene_ref = f"scene://{_sha256(scene_json)}"
    if effective_base_ref is not None and effective_base_ref.get("path") != target:
        raise ToolError(
            "base_diagram_path_mismatch",
            "The draft basename must match the canonical diagram being modified.",
        )
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    claims = {
        "tenant": str(ctx.tenant_id),
        "user": str(ctx.username),
        "chat": str(ctx.chat_id),
        "workspace": str(ctx.wf_id),
        "turn": str(ctx.turn_id),
        "runtime_session": str(ctx.runtime_session_id),
        "draft_id": draft_id,
        "draft_sequence": draft_sequence,
        "draft": path,
        "target": target,
        "source_hash": actual_hash,
        "bundle_hash": actual_hash,
        "scene_ref": scene_ref,
        "spec_hash": spec.spec_hash,
        "base_revision": (effective_base_ref or {}).get("revision"),
        "exp": int(expires.timestamp()),
    }
    check_id = _sign_check(claims)
    check_ref = {
        "check_id": check_id,
        "draft_id": draft_id,
        "draft_sequence": draft_sequence,
        "draft_path": path,
        "target_path": target,
        "checked_source_hash": actual_hash,
        "checked_bundle_hash": actual_hash,
        "scene_ref": scene_ref,
        "spec_hash": spec.spec_hash,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }
    operation, element_ids = _draft_operation(scene, base_scene)
    async with session_scope(tenant_id=str(ctx.tenant_id)) as draft_session:
        ready_status = await DiagramDraftRepo(draft_session).mark_ready(
            draft_id=draft_id,
            sequence=draft_sequence,
            tenant_id=str(ctx.tenant_id),
            workspace_scope_id=str(ctx.wf_id),
            source_hash=actual_hash,
            scene_ref=scene_ref,
            scene_hash=_sha256(scene_json),
            scene_bytes=scene_json,
            operation=operation,
            element_ids=element_ids,
        )
    draft_preview_ref = _draft_preview_ref(
        ctx,
        draft_id=draft_id,
        target_path=target,
        title=document.title,
    )
    result = {
        "status": "ready",
        "source_ref": source_ref,
        "spec_ref": spec_ref,
        "presentable": finalize,
        "checked_source_hash": actual_hash,
        "checked_bundle_hash": actual_hash,
        "scene_ref": scene_ref,
        "draft_id": draft_id,
        "draft_sequence": draft_sequence,
        "draft_revision_status": ready_status,
        "draft_preview_ref": draft_preview_ref,
        "compiler_version": COMPILER_VERSION,
        "theme_version": THEME_VERSION,
        "summary": {
            "family": document.diagram.family,
            "type": document.diagram.type,
            "nodes": len(document.model.nodes),
            "edges": len(document.model.edges),
            "frames": len(document.view.frames),
        },
        "warnings": warnings,
        "auto_repair": scene.auto_repair.model_dump(
            mode="json", by_alias=True
        ),
        "quality": {
            disposition: sum(
                item.get("disposition") == disposition for item in warnings
            )
            for disposition in (
                "blocking", "repairable", "render_cue", "accepted"
            )
        },
        "base_resolution": base_resolution,
        "next": {
            "action": "call_tool" if finalize else "edit_source",
            **({"tool": "present_diagram"} if finalize else {}),
            "reason": (
                "The exact final draft is compile-ready."
                if finalize
                else (
                    "This complete intermediate revision is now visible; "
                    "continue with the next semantic operation."
                )
            ),
            **(
                {
                    "request": {
                        "check_ref": check_ref,
                        "expected_base_revision": (
                            effective_base_ref or {}
                        ).get("revision"),
                    }
                }
                if finalize
                else {}
            ),
        },
    }
    if finalize:
        result["present_request"] = result["next"]["request"]
    return result


@tool(response_format="content_and_artifact")
async def check_diagram(
    source_ref: dict[str, Any],
    spec_ref: dict[str, Any],
    validation_level: Literal["semantic", "compile"] = "compile",
    base_diagram_ref: dict[str, Any] | None = None,
    removed_element_ids: list[str] | None = None,
    finalize: bool = True,
    *,
    runtime: ToolRuntime,
):
    """Validate one exact auto-saved Diagram file against one exact spec.

    New Turns write directly under /data/diagrams and call this tool after each
    coherent update. Legacy resumed draft paths remain accepted internally.
    """
    return await _do_check_diagram(
        source_ref,
        spec_ref,
        validation_level,
        base_diagram_ref,
        runtime,
        removed_element_ids,
        finalize,
    )


async def _canonical_snapshot(ctx, path: str) -> tuple[str | None, bytes | None]:
    async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
        row = (
            await session.execute(
                select(VfsArtifact).where(
                    VfsArtifact.scope_id == str(ctx.wf_id),
                    VfsArtifact.path == path,
                )
            )
        ).scalar_one_or_none()
        revision = vfs_row_revision(row) if row is not None else None
    data = ctx.vfs.read_bytes(wf_id=ctx.wf_id, path=path) if revision else None
    return revision, data


async def _current_diagram_ref(
    ctx,
    path: str,
) -> dict[str, Any] | None:
    """Resolve an exact trusted ref for the current canonical diagram, if any."""
    revision, raw = await _canonical_snapshot(ctx, path)
    if revision is None:
        return None
    if raw is None:
        raise ToolError(
            "canonical_source_unavailable",
            "The canonical diagram revision exists but its source is unavailable.",
        )
    document, issues = parse_and_validate(raw)
    if document is None or any(issue.severity == "error" for issue in issues):
        raise ToolError(
            "invalid_presented_diagram",
            "The canonical diagram no longer passes validation.",
        )
    try:
        scene = compile_diagram(document)
    except DiagramLimitError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    scene_json = scene.model_dump_json(
        by_alias=True,
        exclude_none=True,
    ).encode()
    return _diagram_ref(
        path,
        revision,
        _sha256(raw),
        f"scene://{_sha256(scene_json)}",
    )


async def _canonical_event_id(ctx, path: str) -> int | None:
    async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
        content_revision = (
            await session.execute(
                select(VfsArtifact.content_revision).where(
                    VfsArtifact.scope_id == str(ctx.wf_id),
                    VfsArtifact.path == path,
                )
            )
        ).scalar_one_or_none()
        if content_revision is None:
            return None
        return (
            await session.execute(
                select(VfsArtifactEvent.event_id)
                .where(
                    VfsArtifactEvent.scope_kind == "artifact",
                    VfsArtifactEvent.scope_id == str(ctx.wf_id),
                    VfsArtifactEvent.path == path,
                    VfsArtifactEvent.content_revision == content_revision,
                )
                .order_by(VfsArtifactEvent.event_id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


def _diagram_ref(
    path: str,
    revision: str,
    source_hash: str,
    scene_ref: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "revision": revision,
        "source_hash": source_hash,
        "bundle_hash": source_hash,
        "scene_ref": scene_ref,
        "compiler_version": COMPILER_VERSION,
        "theme_version": THEME_VERSION,
    }


@tool_output(content_type="application/json", tool="present_diagram")
async def _do_present(
    check_ref: dict[str, Any],
    expected_base_revision: str | None,
    runtime: ToolRuntime,
) -> dict:
    ctx = runtime.context
    claims = _verify_check(str(check_ref.get("check_id") or ""), ctx)
    bound_fields = (
        ("draft_id", "draft_id"),
        ("draft_sequence", "draft_sequence"),
        ("draft_path", "draft"),
        ("target_path", "target"),
        ("checked_source_hash", "source_hash"),
        ("checked_bundle_hash", "bundle_hash"),
        ("scene_ref", "scene_ref"),
        ("spec_hash", "spec_hash"),
    )
    for public, claim in bound_fields:
        if check_ref.get(public) != claims.get(claim):
            raise ToolError(
                "invalid_check_ref",
                "The check reference fields were altered; run check_diagram again.",
            )
    _, raw, actual_hash = await _sandbox_source(
        ctx,
        {"path": claims["draft"], "content_hash": claims["source_hash"]},
    )
    document, issues = parse_and_validate(raw)
    if document is None or any(issue.severity == "error" for issue in issues):
        raise ToolError(
            "stale_source",
            "The checked source is no longer compile-ready; run check_diagram again.",
        )
    try:
        scene = compile_diagram(document)
    except DiagramLimitError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    scene_data = scene.model_dump_json(
        by_alias=True, exclude_none=True
    ).encode()
    actual_scene_ref = f"scene://{_sha256(scene_data)}"
    if actual_hash != claims["source_hash"] or actual_scene_ref != claims["scene_ref"]:
        raise ToolError(
            "stale_source",
            "The checked source or compiler result changed; run check_diagram again.",
        )
    signed_base_revision = claims.get("base_revision")
    if expected_base_revision != signed_base_revision:
        raise ToolError(
            "invalid_expected_base_revision",
            "expected_base_revision must be copied unchanged from check_diagram.",
        )
    sandbox_session = await _require_session(ctx)
    fence = getattr(sandbox_session, "fence_external_vfs_path", None)
    if fence is not None and not await fence(claims["target"]):
        raise ToolError(
            "presented_session_rehydrate_required",
            "The resident sandbox could not fence the canonical diagram path; "
            "rehydrate it before presenting.",
        )
    current_revision, current_data = await _canonical_snapshot(ctx, claims["target"])
    active_ref_committed = False
    if current_revision and _sha256(current_data or b"") == actual_hash:
        async with session_scope(tenant_id=str(ctx.tenant_id)) as draft_session:
            try:
                await DiagramDraftRepo(draft_session).mark_terminal(
                    draft_id=str(claims["draft_id"]),
                    sequence=int(claims["draft_sequence"]),
                    status="committed",
                )
            except ValueError as exc:
                raise ToolError(
                    "stale_diagram_draft_revision",
                    "A newer ready Diagram revision exists; check and present "
                    "the latest source instead.",
                ) from exc
        revision = current_revision
        event_id = await _canonical_event_id(ctx, claims["target"])
    else:
        async with session_scope(tenant_id=str(ctx.tenant_id)) as write_session:
            try:
                await DiagramDraftRepo(write_session).mark_terminal(
                    draft_id=str(claims["draft_id"]),
                    sequence=int(claims["draft_sequence"]),
                    status="committed",
                )
            except ValueError as exc:
                raise ToolError(
                    "stale_diagram_draft_revision",
                    "A newer ready Diagram revision exists; check and present "
                    "the latest source instead.",
                ) from exc
            repo = VfsRepo(write_session, object_store=get_object_store())
            committed = await repo.compare_and_swap_artifact_bytes(
                wf_id=str(ctx.wf_id),
                tenant=str(ctx.tenant_id),
                path=claims["target"],
                expected_revision=signed_base_revision,
                data=raw,
                content_type="application/vnd.vibecanvas.diagram+json",
                abstract=document.title,
            )
            if not committed.committed:
                raise ToolError(
                    "revision_conflict",
                    "Canonical revision changed: expected "
                    f"{signed_base_revision}, current {committed.current_revision}.",
                )
            revision = committed.revision
            event_id = (
                await write_session.execute(
                    select(VfsArtifactEvent.event_id)
                    .where(
                        VfsArtifactEvent.scope_kind == "artifact",
                        VfsArtifactEvent.scope_id == str(ctx.wf_id),
                        VfsArtifactEvent.path == claims["target"],
                        VfsArtifactEvent.content_revision
                        == committed.content_revision,
                    )
                    .order_by(VfsArtifactEvent.event_id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            assert revision is not None
            await ChatRepo(write_session, str(ctx.username)).set_active_diagram(
                str(ctx.chat_id),
                _diagram_ref(
                    claims["target"],
                    revision,
                    actual_hash,
                    actual_scene_ref,
                ),
                family=document.diagram.family,
                diagram_type=document.diagram.type,
            )
            active_ref_committed = True
        acknowledge = getattr(
            sandbox_session,
            "acknowledge_external_vfs_commit",
            sandbox_session.mirror_vfs_write,
        )
        if not await acknowledge(claims["target"], raw):
            raise ToolError(
                "presented_session_rehydrate_required",
                "The diagram was committed, but the resident sandbox must be "
                "rehydrated before more writes.",
            )
    assert revision is not None
    ref = _diagram_ref(claims["target"], revision, actual_hash, actual_scene_ref)
    if not active_ref_committed:
        async with session_scope(tenant_id=str(ctx.tenant_id)) as chat_session:
            await ChatRepo(chat_session, str(ctx.username)).set_active_diagram(
                str(ctx.chat_id),
                ref,
                family=document.diagram.family,
                diagram_type=document.diagram.type,
            )
    preview_ref = {
        "schemaVersion": 1,
        "kind": "file",
        "fileRef": {
            "schemaVersion": 1,
            "scope": "chat",
            "chatId": str(ctx.chat_id),
            "path": claims["target"],
        },
    }
    review_request = {
        "diagram_ref": ref,
        "focus": {"mode": "canvas"},
        "purpose": (
            "major_change" if signed_base_revision is not None else "initial"
        ),
        "theme": "light",
        "detail": "normal",
    }
    return {
        "status": "presented",
        "diagram_ref": ref,
        "preview_ref": preview_ref,
        "review_request": review_request,
        "delivery": {
            "mode": "vfs_event",
            "revision": revision,
            "event_id": event_id,
        },
        "next": {
            "action": "call_tool",
            "tool": "review_diagram",
            "request": review_request,
        },
    }


@tool(response_format="content_and_artifact")
async def present_diagram(
    check_ref: dict[str, Any],
    expected_base_revision: str | None = None,
    *,
    runtime: ToolRuntime,
):
    """Promote one unaltered compile-ready draft to the canonical diagram path.

    Accept only check_ref and expected_base_revision copied unchanged from a
    successful check_diagram present_request. The operation is idempotent for
    the same source and refuses stale revisions rather than overwriting them.
    """
    return await _do_present(check_ref, expected_base_revision, runtime)


async def _load_presented(ctx, diagram_ref: dict[str, Any]):
    path = str(diagram_ref.get("path") or "")
    if (
        not path.startswith("/data/diagrams/")
        or not path.endswith(".vdiagram.json")
        or ".." in path.split("/")
    ):
        raise ToolError(
            "invalid_diagram_ref",
            "diagram_ref must reference /data/diagrams/*.vdiagram.json.",
        )
    revision, raw = await _canonical_snapshot(ctx, path)
    if (
        revision != diagram_ref.get("revision")
        or raw is None
        or _sha256(raw) != diagram_ref.get("source_hash")
    ):
        raise ToolError(
            "stale_diagram_ref",
            "The exact presented diagram revision is unavailable or changed.",
        )
    document, issues = parse_and_validate(raw)
    if document is None or any(issue.severity == "error" for issue in issues):
        raise ToolError(
            "invalid_presented_diagram",
            "The presented diagram no longer passes validation.",
        )
    try:
        scene = compile_diagram(document)
    except DiagramLimitError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    scene_json = scene.model_dump_json(
        by_alias=True, exclude_none=True
    ).encode()
    scene_ref = f"scene://{_sha256(scene_json)}"
    if scene_ref != diagram_ref.get("scene_ref"):
        raise ToolError(
            "compiler_version_mismatch",
            "The diagram must be presented again with the current compiler.",
        )
    return document, scene, raw


@tool_output(content_type="application/json", tool="inspect_diagram")
async def _do_inspect(
    diagram_ref: dict[str, Any],
    selector: dict[str, Any],
    include: list[str],
    runtime: ToolRuntime,
) -> dict:
    document, scene, _ = await _load_presented(runtime.context, diagram_ref)
    mode = selector.get("mode", "summary")
    candidates = scene.nodes
    truncated = False
    if mode == "query":
        query = str(selector.get("query") or "").lower()
        matched = [
            node
            for node in candidates
            if query in node.label.lower() or query in node.id.lower()
        ]
        limit = max(1, min(int(selector.get("limit", 20)), 100))
        truncated = len(matched) > limit
        candidates = matched[:limit]
    elif mode == "elements":
        ids = set(selector.get("element_ids") or [])
        candidates = [node for node in candidates if node.id in ids]
    elif mode == "group":
        group_id = str(selector.get("group_id") or "")
        group = next(
            (item for item in document.model.groups if item.id == group_id),
            None,
        )
        if group is None:
            raise ToolError("group_not_found", f"Group '{group_id}' does not exist.")
        ids = set(group.node_ids)
        candidates = [node for node in candidates if node.id in ids]
    elif mode == "region":
        bounds = selector.get("bounds") or {}
        left = float(bounds.get("x", 0))
        top = float(bounds.get("y", 0))
        right = left + float(bounds.get("width", 0))
        bottom = top + float(bounds.get("height", 0))
        candidates = [
            node
            for node in candidates
            if node.bounds.x < right
            and node.bounds.x + node.bounds.width > left
            and node.bounds.y < bottom
            and node.bounds.y + node.bounds.height > top
        ]
    elif mode != "summary":
        raise ToolError(
            "unsupported_selector",
            "Inspect supports summary, query, elements, group, and region selectors.",
        )
    matches = []
    for node in candidates:
        override = document.view.overrides.get(node.id)
        match = {
            "element_id": node.id,
        }
        if "semantics" in include:
            match["semantic"] = {
                "kind": node.kind,
                "label": node.label,
                "description": node.description,
            }
        if "relations" in include:
            match["relations"] = {
                "incoming": [
                    edge.id for edge in scene.edges if edge.target == node.id
                ],
                "outgoing": [
                    edge.id for edge in scene.edges if edge.source == node.id
                ],
            }
        if "ownership" in include:
            match["ownership"] = {
                "pinned": bool(override and override.pinned),
                "owner": override.owner if override else "compiler",
            }
        if "bounds" in include:
            match["bounds"] = node.bounds.model_dump()
        if "source_locations" in include:
            match["source"] = {"json_pointer": node.source_pointer}
        if "constraints" in include:
            match["constraints"] = [
                constraint.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
                for constraint in document.intent.constraints
                if node.id in _constraint_node_ids(constraint)
            ]
        matches.append(match)
    return {
        "status": "ok",
        "diagram_ref": diagram_ref,
        "summary": {
            "family": scene.family,
            "type": scene.diagram_type,
            "nodes": len(scene.nodes),
            "edges": len(scene.edges),
        },
        "matches": matches,
        "truncated": truncated,
        "next": {
            "action": "edit_source",
            "reason": "Edit this exact auto-saved source file in place.",
            "read_source_path": diagram_ref["path"],
            "write_source_path": diagram_ref["path"],
            "expected_base_revision": diagram_ref["revision"],
            "preserve_element_ids": [node.id for node in candidates],
            "retained_layout": _retained_layout_seed(document, scene),
            "then_workflow": [
                "check_diagram",
                "render_interactive",
                "review_diagram",
            ],
        },
    }


@tool(response_format="content_and_artifact")
async def inspect_diagram(
    diagram_ref: dict[str, Any],
    selector: dict[str, Any] | None = None,
    include: list[str] | None = None,
    *,
    runtime: ToolRuntime,
):
    """Inspect stable IDs, relations, ownership and source pointers for one exact presented revision."""
    return await _do_inspect(
        diagram_ref,
        selector or {"mode": "summary"},
        include or [
            "semantics",
            "relations",
            "ownership",
            "source_locations",
            "bounds",
        ],
        runtime,
    )


def _padded_bounds(items: list[SceneBounds], padding: float = 32) -> SceneBounds:
    min_x = min(item.x for item in items) - padding
    min_y = min(item.y for item in items) - padding
    max_x = max(item.x + item.width for item in items) + padding
    max_y = max(item.y + item.height for item in items) + padding
    return SceneBounds(
        x=min_x,
        y=min_y,
        width=max_x - min_x,
        height=max_y - min_y,
    )


def _scene_for_focus(
    scene: DiagramScene,
    focus: dict[str, Any],
    *,
    allow_region: bool,
) -> tuple[DiagramScene, SceneBounds, list[str]]:
    mode = str(focus.get("mode") or "canvas")
    if mode == "canvas":
        focus_bounds = scene.bounds
    elif mode == "elements":
        requested = list(dict.fromkeys(focus.get("element_ids") or []))
        if not requested:
            raise ToolError(
                "invalid_focus_elements",
                "Element focus requires at least one element ID.",
            )
        selected = [node for node in scene.nodes if node.id in requested]
        if len(selected) != len(requested):
            found = {node.id for node in selected}
            missing = [node_id for node_id in requested if node_id not in found]
            raise ToolError(
                "focus_element_not_found",
                f"Focus elements do not exist: {', '.join(missing)}.",
            )
        focus_bounds = _padded_bounds([node.bounds for node in selected])
    elif mode == "frame":
        frame_id = str(focus.get("frame_id") or "")
        frame = next(
            (group for group in scene.groups if group.id == frame_id),
            None,
        )
        if frame is None:
            raise ToolError(
                "focus_frame_not_found",
                f"Focus frame '{frame_id}' does not exist.",
            )
        focus_bounds = _padded_bounds([frame.bounds])
    elif mode == "region" and allow_region:
        raw = focus.get("bounds") or {}
        focus_bounds = SceneBounds.model_validate(raw)
        if focus_bounds.width <= 0 or focus_bounds.height <= 0:
            raise ToolError(
                "invalid_focus_region",
                "Focus region width and height must be positive.",
            )
    else:
        supported = "canvas, frame, elements, or region" if allow_region else (
            "canvas, frame, or elements"
        )
        raise ToolError(
            "unsupported_focus",
            f"Focus mode must be {supported}.",
        )
    right = focus_bounds.x + focus_bounds.width
    bottom = focus_bounds.y + focus_bounds.height
    visible_ids = [
        node.id
        for node in scene.nodes
        if node.bounds.x < right
        and node.bounds.x + node.bounds.width > focus_bounds.x
        and node.bounds.y < bottom
        and node.bounds.y + node.bounds.height > focus_bounds.y
    ]
    return (
        scene.model_copy(update={"bounds": focus_bounds}),
        focus_bounds,
        visible_ids,
    )


def _review_issue_is_actionable(
    issue: dict[str, Any],
    *,
    preserve_layout: bool = False,
) -> bool:
    """Return whether visual review must start another canonical edit cycle."""
    disposition = str(issue.get("disposition") or "")
    if disposition == "blocking":
        return True
    if disposition != "repairable":
        return False
    return not (
        preserve_layout and issue.get("code") == "constraint_unsatisfied"
    )


@tool_error_boundary(tool="review_diagram")
async def _do_review(
    diagram_ref: dict[str, Any],
    focus: dict[str, Any],
    purpose: str,
    theme: str,
    detail: str,
    runtime: ToolRuntime,
):
    _, scene, _ = await _load_presented(runtime.context, diagram_ref)
    focused_scene, focus_bounds, visible_ids = _scene_for_focus(
        scene,
        focus,
        allow_region=True,
    )
    try:
        png = await render_scene_png_isolated(
            focused_scene,
            theme=theme if theme in {"light", "dark", "print"} else "light",
            max_width=2400 if detail == "high" else 1600,
            max_height=1600 if detail == "high" else 1000,
        )
    except DiagramLimitError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    image = Image.open(io.BytesIO(png))
    review_id = f"review_{hashlib.sha256(png).hexdigest()[:16]}"
    review_path = f"/run/diagram-reviews/{review_id}.png"
    sandbox_review_path = f"/memory/diagram-review-artifacts/{review_id}.png"
    sandbox_session = await _require_session(runtime.context)
    sandbox_write = await sandbox_session.write_bytes(sandbox_review_path, png)
    if not sandbox_write.get("ok"):
        raise ToolError(
            "review_artifact_write_failed",
            "The review image could not be materialized in the Chat workspace.",
        )
    async with session_scope(tenant_id=str(runtime.context.tenant_id)) as session:
        review_repo = VfsRunRepo(
            session,
            get_object_store(),
            str(runtime.context.tenant_id),
        )
        try:
            existing_review = await review_repo.read_bytes(
                run_id=str(runtime.context.turn_id), path=review_path
            )
        except KeyError:
            existing_review = None
        if existing_review != png:
            await review_repo.write_bytes(
                run_id=str(runtime.context.turn_id),
                path=review_path,
                data=png,
                content_type="image/png",
                abstract=f"Diagram review: {scene.title}",
                wf_id=str(runtime.context.wf_id),
            )
    issues = [item.model_dump(mode="json", by_alias=True) for item in scene.issues]
    actionable_issues = [
        item
        for item in issues
        if _review_issue_is_actionable(
            item,
            preserve_layout=purpose == "major_change",
        )
    ]
    preview_ref = {
        "schemaVersion": 1,
        "kind": "file",
        "fileRef": {
            "schemaVersion": 1,
            "scope": "chat",
            "chatId": str(runtime.context.chat_id),
            "path": diagram_ref["path"],
        },
    }
    review_image = {
        "role": "overview",
        "mime_type": "image/png",
        "width": image.width,
        "height": image.height,
        "content_hash": _sha256(png),
        "artifact_ref": (
            f"vibecanvas://run/{runtime.context.turn_id}/"
            f"diagram-reviews/{review_id}"
        ),
        "sandbox_path": sandbox_review_path,
    }
    quality = {
        disposition: sum(
            item.get("disposition") == disposition for item in issues
        )
        for disposition in (
            "blocking", "repairable", "render_cue", "accepted"
        )
    }
    structured = {
        "status": "reviewed",
        "review_id": review_id,
        "diagram_ref": diagram_ref,
        "renders": [review_image],
        "review_images": [review_image],
        "visual_metrics": {
            "edge_crossings": sum(
                item["code"] == "edge_crossing" for item in issues
            ),
            "overlap_count": sum(
                item["code"] == "node_overlap" for item in issues
            ),
            "clipped_label_count": sum(
                item["code"] == "label_clipped" for item in issues
            ),
            "canvas_aspect_ratio": round(
                image.width / max(1, image.height), 3
            ),
            "minimum_rendered_font_px": 9,
        },
        "visual_issues": issues,
        "quality": quality,
        "agent_action_required": bool(actionable_issues),
        "agent_issues": actionable_issues,
        "render_hints": [
            {
                "issue_id": item.get("issue_id"),
                "code": item.get("code"),
                "element_ids": item.get("element_ids") or [],
                "geometry": item.get("geometry") or {},
            }
            for item in issues
            if item.get("disposition") == "render_cue"
        ],
        "review_context": {
            "focus_bounds": focus_bounds.model_dump(),
            "visible_element_ids": visible_ids,
            "selected_element_ids": (
                list(focus.get("element_ids") or [])
                if focus.get("mode") == "elements"
                else []
            ),
        },
        "image_delivery": {
            "mode": "on_demand_artifact_ref",
            "delivered_to_model": False,
            "image_count": 1,
            "instruction": (
                "Use the current Runtime's image-reading tool on sandbox_path "
                "when structured evidence is insufficient or the user asks "
                "you to view or answer a question about the rendered image. "
                "In that case, read the latest image and include the concrete "
                "visual answer; do not claim pixel review or return only a "
                "generic completion statement without reading it."
            ),
        },
        "next": {
            "action": "call_tool" if actionable_issues else "deliver",
            "reason": (
                "The presented revision has actionable visual issues."
                if actionable_issues
                else (
                    "The rendered revision is ready to deliver with "
                    "non-blocking warnings disclosed."
                    if issues
                    else "The rendered revision is ready to deliver."
                )
            ),
            "repair_element_ids": [
                item.get("element_id")
                for item in actionable_issues
                if item.get("element_id")
            ],
            **(
                {
                    "tool": "inspect_diagram",
                    "request": {
                        "diagram_ref": diagram_ref,
                        "selector": {"mode": "summary"},
                        "include": [
                            "semantics",
                            "relations",
                            "constraints",
                            "ownership",
                            "source_locations",
                        ],
                    },
                    "then_workflow": [
                        "inspect_diagram",
                        "edit_source",
                        "check_diagram",
                        "render_interactive",
                        "review_diagram",
                    ],
                    "max_remaining_visual_iterations": 2,
                }
                if actionable_issues
                else {"preview_ref": preview_ref}
            ),
        },
    }
    content = json.dumps(structured, ensure_ascii=False)
    artifact = {
        "schema_version": 1,
        "status": "ok",
        "content": content,
        "content_abstract": f"Reviewed {scene.title}",
        "payload": {"kind": "inline"},
        "meta": {
            "tool": "review_diagram",
            "content_type": "application/json",
        },
        "structured_content": structured,
    }
    return content, artifact


@tool(response_format="content_and_artifact")
async def review_diagram(
    diagram_ref: dict[str, Any],
    focus: dict[str, Any] | None = None,
    purpose: Literal["initial", "major_change", "final", "diagnose"] = "final",
    theme: Literal["light", "dark", "print"] = "light",
    detail: Literal["normal", "high"] = "normal",
    *,
    runtime: ToolRuntime,
):
    """Review one exact revision with structured issues and on-demand image refs."""
    return await _do_review(
        diagram_ref,
        focus or {"mode": "canvas"},
        purpose,
        theme,
        detail,
        runtime,
    )


@tool_error_boundary(tool="read_diagram_review_image")
async def _do_read_diagram_review_image(
    sandbox_path: str,
    runtime: ToolRuntime,
):
    if not _REVIEW_IMAGE_PATH.fullmatch(sandbox_path):
        raise ToolError(
            "invalid_review_image_path",
            "Copy sandbox_path unchanged from the latest review_images entry.",
        )
    sandbox_session = await _require_session(runtime.context)
    result = await sandbox_session.read_bytes(sandbox_path)
    if not result.get("ok"):
        raise ToolError(
            "review_image_not_found",
            "The review image is unavailable; rerun review_diagram for the "
            "latest revision and retry with its sandbox_path.",
        )
    data = result.get("data")
    if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ToolError(
            "invalid_review_image",
            "The referenced review artifact is not a valid PNG image.",
        )
    structured = {
        "status": "ok",
        "sandbox_path": sandbox_path,
        "mime_type": "image/png",
        "bytes": len(data),
        "content_hash": _sha256(data),
        "instruction": (
            "Inspect the attached image and answer the user's concrete visual "
            "question before reporting Diagram completion."
        ),
    }
    content = json.dumps(structured, ensure_ascii=False)
    return content, {
        "schema_version": 1,
        "status": "ok",
        "content": content,
        "content_abstract": "Diagram review image attached",
        "payload": {"kind": "inline"},
        "meta": {
            "tool": "read_diagram_review_image",
            "content_type": "application/json",
            "mcp_content": [{
                "type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mime_type": "image/png",
            }],
        },
        "structured_content": structured,
    }


@tool(response_format="content_and_artifact")
async def read_diagram_review_image(
    sandbox_path: str,
    *,
    runtime: ToolRuntime,
):
    """Read one exact review image when the Runtime has no native image tool."""
    return await _do_read_diagram_review_image(sandbox_path, runtime)


@tool_output(content_type="application/json", tool="export_diagram")
async def _do_export(
    diagram_ref: dict[str, Any],
    format: str,
    focus: dict[str, Any],
    theme: str,
    scale: float,
    background: str,
    output_basename: str,
    runtime: ToolRuntime,
) -> dict:
    if not _SAFE_BASENAME.fullmatch(output_basename):
        raise ToolError(
            "invalid_output_basename",
            "output_basename must be a safe filename fragment without path separators.",
        )
    _, scene, _ = await _load_presented(runtime.context, diagram_ref)
    focused_scene, _, _ = _scene_for_focus(
        scene,
        focus,
        allow_region=False,
    )
    try:
        if format == "svg":
            data, mime = await render_scene_svg_isolated(
                focused_scene,
                theme=theme,
                background=background,
            ), "image/svg+xml"
        elif format == "png":
            data = await render_scene_png_isolated(
                focused_scene,
                theme=theme,
                max_width=min(2400, round(1600 * scale)),
                max_height=min(1600, round(1000 * scale)),
                background=background,
            )
            mime = "image/png"
        elif format == "pdf":
            data, mime = await render_scene_pdf_isolated(
                focused_scene,
                theme=theme,
                background=background,
            ), "application/pdf"
        else:
            raise ToolError(
                "unsupported_export_format",
                "format must be svg, png, or pdf.",
            )
    except DiagramLimitError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    revision_tag = str(diagram_ref["revision"]).replace("sha256:", "")[:10]
    option_payload = json.dumps(
        {
            "format": format,
            "focus": focus,
            "theme": theme,
            "scale": scale,
            "background": background,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    option_tag = hashlib.sha256(option_payload).hexdigest()[:10]
    stem = f"{output_basename}-r{revision_tag}-{theme}-{option_tag}"
    sequence = 1
    cached = False
    while True:
        if sequence > 100:
            raise ToolError(
                "export_name_exhausted",
                "Unable to allocate a non-overwriting export filename.",
            )
        suffix = "" if sequence == 1 else f"-{sequence}"
        path = f"/data/exports/{stem}{suffix}.{format}"
        current_revision, current_data = await _canonical_snapshot(
            runtime.context,
            path,
        )
        if current_data is not None:
            if _sha256(current_data) == _sha256(data):
                cached = True
                break
            sequence += 1
            continue
        committed = runtime.context.vfs.compare_and_swap_artifact_bytes(
            wf_id=runtime.context.wf_id,
            path=path,
            expected_revision=None,
            data=data,
            content_type=mime,
            abstract=f"Export of {scene.title}",
        )
        if not committed.committed:
            sequence += 1
            continue
        current_revision = committed.revision
        cached = False
        session = await _require_session(runtime.context)
        acknowledge = getattr(
            session,
            "acknowledge_external_vfs_commit",
            session.mirror_vfs_write,
        )
        if not await acknowledge(path, data):
            raise ToolError(
                "exported_session_rehydrate_required",
                "The export was committed, but the resident sandbox must be "
                "rehydrated before more writes.",
            )
        break
    fidelity = (
        "native"
        if format == "svg"
        else "rasterized"
        if format == "pdf"
        else "raster"
    )
    return {
        "status": "exported",
        "diagram_ref": diagram_ref,
        "export": {
            "path": path,
            "revision": current_revision,
            "format": format,
            "mime_type": mime,
            "bytes": len(data),
            "content_hash": _sha256(data),
            "vector_fidelity": fidelity,
            "cached": (
                cached
            ),
        },
        "download_ref": {
            "kind": "vfs_file",
            "path": path,
            "revision": current_revision,
        },
        "warnings": [],
        "next": {"action": "deliver", "artifact": "download_ref"},
    }


@tool(response_format="content_and_artifact")
async def export_diagram(
    diagram_ref: dict[str, Any],
    format: Literal["svg", "png", "pdf"],
    focus: dict[str, Any] | None = None,
    theme: Literal["light"] = "light",
    scale: float = 1.0,
    background: Literal["white"] = "white",
    output_basename: str = "diagram",
    *,
    runtime: ToolRuntime,
):
    """Export one exact presented revision to /data/exports as SVG, PNG, or PDF."""
    return await _do_export(
        diagram_ref,
        format,
        focus or {"mode": "canvas"},
        theme,
        max(0.5, min(scale, 2.0)),
        background,
        output_basename,
        runtime,
    )


DIAGRAM_TOOLS = [
    get_diagram_spec,
    search_diagram_assets,
    inspect_diagram,
    check_diagram,
    review_diagram,
    read_diagram_review_image,
    export_diagram,
]

# The machine-readable Agent Contract is the single source for the real MCP
# descriptions.  The Python docstrings remain developer documentation only.
for _diagram_tool in DIAGRAM_TOOLS:
    _diagram_tool.description = diagram_tool_description(
        str(_diagram_tool.name)
    )

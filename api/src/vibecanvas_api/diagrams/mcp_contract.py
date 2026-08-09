"""Strict JSON Schemas for the public Diagram Platform MCP boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_HASH = {"type": "string", "pattern": r"^sha256:[a-f0-9]{64}$"}
_NON_EMPTY = {"type": "string", "minLength": 1}
_NULLABLE_HASH = {"anyOf": [_HASH, {"type": "null"}]}

SOURCE_REF = _object(
    {
        "path": {
            "type": "string",
            "pattern": (
                r"^/(?:data/diagrams|memory/diagram-drafts)/"
                r"[^/]+\.vdiagram\.json$"
            ),
            "description": (
                "Use the exact auto-saved /data/diagrams path. The memory "
                "draft prefix is accepted only for legacy resumed Turns."
            ),
        },
        "content_hash": {
            **_HASH,
            "description": (
                "Copy content_hash from the same filesystem write without "
                "recomputing it."
            ),
        },
    },
    required=("path", "content_hash"),
)

SPEC_REF = _object(
    {
        # The registry version/hash are the security-relevant bindings below.
        # Accept the common JSON-client rendering of a numeric const as "1"
        # so one harmless representation difference cannot break the otherwise
        # opaque copy-forward chain.
        "schema_version": {
            "oneOf": [{"const": 1}, {"const": "1"}],
        },
        "family": _NON_EMPTY,
        "type": _NON_EMPTY,
        "spec_version": _NON_EMPTY,
        "spec_hash": _HASH,
    },
    required=(
        "schema_version",
        "family",
        "type",
        "spec_version",
        "spec_hash",
    ),
)

DIAGRAM_REF = _object(
    {
        "path": {
            "type": "string",
            "pattern": r"^/data/diagrams/[^/]+\.vdiagram\.json$",
        },
        "revision": _HASH,
        "source_hash": _HASH,
        "bundle_hash": _HASH,
        "scene_ref": {
            "type": "string",
            "pattern": r"^scene://sha256:[a-f0-9]{64}$",
        },
        "compiler_version": _NON_EMPTY,
        "theme_version": _NON_EMPTY,
    },
    required=(
        "path",
        "revision",
        "source_hash",
        "bundle_hash",
        "scene_ref",
        "compiler_version",
        "theme_version",
    ),
)

CHECK_REF = _object(
    {
        "check_id": {"type": "string", "pattern": r"^dcheck_"},
        "draft_id": _NON_EMPTY,
        "draft_sequence": {"type": "integer", "minimum": 1},
        "draft_path": SOURCE_REF["properties"]["path"],
        "target_path": DIAGRAM_REF["properties"]["path"],
        "checked_source_hash": _HASH,
        "checked_bundle_hash": _HASH,
        "scene_ref": DIAGRAM_REF["properties"]["scene_ref"],
        "spec_hash": _HASH,
        "expires_at": {"type": "string", "format": "date-time"},
    },
    required=(
        "check_id",
        "draft_id",
        "draft_sequence",
        "draft_path",
        "target_path",
        "checked_source_hash",
        "checked_bundle_hash",
        "scene_ref",
        "spec_hash",
        "expires_at",
    ),
)

CANVAS_FOCUS = _object({"mode": {"const": "canvas"}}, required=("mode",))
FRAME_FOCUS = _object(
    {"mode": {"const": "frame"}, "frame_id": _NON_EMPTY},
    required=("mode", "frame_id"),
)
ELEMENTS_FOCUS = _object(
    {
        "mode": {"const": "elements"},
        "element_ids": {
            "type": "array",
            "items": _NON_EMPTY,
            "minItems": 1,
            "maxItems": 100,
            "uniqueItems": True,
        },
    },
    required=("mode", "element_ids"),
)
REGION_FOCUS = _object(
    {
        "mode": {"const": "region"},
        "bounds": _object(
            {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number", "exclusiveMinimum": 0},
                "height": {"type": "number", "exclusiveMinimum": 0},
            },
            required=("x", "y", "width", "height"),
        ),
    },
    required=("mode", "bounds"),
)
REVIEW_FOCUS = {
    "oneOf": [CANVAS_FOCUS, FRAME_FOCUS, ELEMENTS_FOCUS, REGION_FOCUS],
    "discriminator": {"propertyName": "mode"},
}
EXPORT_FOCUS = {
    "oneOf": [CANVAS_FOCUS, FRAME_FOCUS, ELEMENTS_FOCUS],
    "discriminator": {"propertyName": "mode"},
}

INSPECT_SELECTOR = {
    "oneOf": [
        _object({"mode": {"const": "summary"}}, required=("mode",)),
        _object(
            {
                "mode": {"const": "query"},
                "query": _NON_EMPTY,
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            required=("mode", "query"),
        ),
        _object(
            {
                "mode": {"const": "elements"},
                "element_ids": ELEMENTS_FOCUS["properties"]["element_ids"],
            },
            required=("mode", "element_ids"),
        ),
        _object(
            {"mode": {"const": "group"}, "group_id": _NON_EMPTY},
            required=("mode", "group_id"),
        ),
        REGION_FOCUS,
    ],
    "discriminator": {"propertyName": "mode"},
}


DIAGRAM_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_diagram_spec": _object(
        {
            "family": {
                **_NON_EMPTY,
                "description": "Copy one exact family from the enabled command catalog.",
            },
            "diagram_type": {
                **_NON_EMPTY,
                "description": "Copy its exact type from the same catalog entry.",
            },
            "schema_version": {"const": 1, "default": 1},
        },
        required=("family", "diagram_type"),
    ),
    "search_diagram_assets": _object(
        {
            "query": {"type": "string", "maxLength": 200},
            "family": _NON_EMPTY,
            "diagram_type": _NON_EMPTY,
            "asset_kinds": {
                "type": "array",
                "items": {"enum": ["shape", "icon"]},
                "uniqueItems": True,
                "default": [],
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
            },
        },
        required=("query", "family", "diagram_type"),
    ),
    "inspect_diagram": _object(
        {
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "selector": {**deepcopy(INSPECT_SELECTOR), "default": {"mode": "summary"}},
            "include": {
                "type": "array",
                "items": {
                    "enum": [
                        "semantics",
                        "relations",
                        "ownership",
                        "source_locations",
                        "bounds",
                        "constraints",
                    ]
                },
                "uniqueItems": True,
                "default": [
                    "semantics",
                    "relations",
                    "ownership",
                    "source_locations",
                    "bounds",
                ],
            },
        },
        required=("diagram_ref",),
    ),
    "check_diagram": _object(
        {
            "source_ref": deepcopy(SOURCE_REF),
            "spec_ref": deepcopy(SPEC_REF),
            "validation_level": {
                "enum": ["semantic", "compile"],
                "default": "compile",
            },
            "base_diagram_ref": {
                "anyOf": [deepcopy(DIAGRAM_REF), {"type": "null"}],
                "default": None,
            },
            "removed_element_ids": {
                "type": "array",
                "items": _NON_EMPTY,
                "uniqueItems": True,
                "maxItems": 100,
                "default": [],
                "description": (
                    "For a modification, list only stable node IDs the user "
                    "explicitly asked to delete or replace. Every omitted base "
                    "node ID is protected and must remain in the draft."
                ),
            },
        },
        required=("source_ref", "spec_ref"),
    ),
    "present_diagram": _object(
        {
            "check_ref": deepcopy(CHECK_REF),
            "expected_base_revision": {
                **deepcopy(_NULLABLE_HASH),
                "default": None,
            },
        },
        required=("check_ref",),
    ),
    "review_diagram": _object(
        {
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "focus": {**deepcopy(REVIEW_FOCUS), "default": {"mode": "canvas"}},
            "purpose": {
                "enum": ["initial", "major_change", "final", "diagnose"],
                "default": "final",
            },
            "theme": {"enum": ["light", "dark", "print"], "default": "light"},
            "detail": {"enum": ["normal", "high"], "default": "normal"},
        },
        required=("diagram_ref",),
    ),
    "read_diagram_review_image": _object(
        {
            "sandbox_path": {
                "type": "string",
                "pattern": (
                    r"^/memory/diagram-review-artifacts/"
                    r"review_[a-f0-9]{16}\.png$"
                ),
            },
        },
        required=("sandbox_path",),
    ),
    "export_diagram": _object(
        {
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "format": {"enum": ["svg", "png", "pdf"]},
            "focus": {**deepcopy(EXPORT_FOCUS), "default": {"mode": "canvas"}},
            "theme": {"enum": ["light"], "default": "light"},
            "scale": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 2.0,
                "default": 1.0,
            },
            "background": {
                "enum": ["white"],
                "default": "white",
            },
            "output_basename": {
                "type": "string",
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$",
                "default": "diagram",
            },
        },
        required=("diagram_ref", "format"),
    ),
}


def _loose_object() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


DIAGRAM_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_diagram_spec": _object(
        {
            "status": {"const": "ok"},
            "spec_ref": deepcopy(SPEC_REF),
            "selection_rationale": _loose_object(),
            "document_contract": _loose_object(),
            "authoring_schema": _loose_object(),
            "authoring_instructions": {"type": "array", "items": {"type": "string"}},
            "forbidden_patterns": {"type": "array", "items": {"type": "string"}},
            "recommended_layout": _loose_object(),
            "allowed_node_kinds": {"type": "array", "items": {"type": "string"}},
            "allowed_edge_kinds": {"type": "array", "items": {"type": "string"}},
            "allowed_constraints": {"type": "array", "items": {"type": "string"}},
            "required_semantics": {"type": "array", "items": {"type": "string"}},
            "quality_rules": {"type": "array", "items": {"type": "string"}},
            "quality_policy": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "asset_policy": _loose_object(),
            "minimal_example": _loose_object(),
            "next": _loose_object(),
        },
        required=(
            "status",
            "spec_ref",
            "selection_rationale",
            "document_contract",
            "authoring_schema",
            "authoring_instructions",
            "forbidden_patterns",
            "recommended_layout",
            "allowed_node_kinds",
            "allowed_edge_kinds",
            "allowed_constraints",
            "required_semantics",
            "quality_rules",
            "quality_policy",
            "asset_policy",
            "minimal_example",
            "next",
        ),
    ),
    "search_diagram_assets": _object(
        {
            "status": {"const": "ok"},
            "catalog_version": _NON_EMPTY,
            "assets": {"type": "array", "items": _loose_object()},
            "next": _loose_object(),
        },
        required=("status", "catalog_version", "assets", "next"),
    ),
    "inspect_diagram": _object(
        {
            "status": {"const": "ok"},
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "summary": _loose_object(),
            "matches": {"type": "array", "items": _loose_object()},
            "truncated": {"type": "boolean"},
            "next": _loose_object(),
        },
        required=("status", "diagram_ref", "summary", "matches", "truncated", "next"),
    ),
    "check_diagram": _object(
        {
            "status": {"enum": ["invalid", "valid", "ready"]},
            "source_ref": deepcopy(SOURCE_REF),
            "spec_ref": deepcopy(SPEC_REF),
            "presentable": {"type": "boolean"},
            "issues": {"type": "array", "items": _loose_object()},
            "warnings": {"type": "array", "items": _loose_object()},
            "checked_source_hash": _HASH,
            "checked_bundle_hash": _HASH,
            "scene_ref": DIAGRAM_REF["properties"]["scene_ref"],
            "compiler_version": _NON_EMPTY,
            "theme_version": _NON_EMPTY,
            "draft_id": _NON_EMPTY,
            "draft_sequence": {"type": "integer", "minimum": 1},
            "draft_revision_status": {"enum": ["ready", "superseded"]},
            "draft_preview_ref": _loose_object(),
            "summary": _loose_object(),
            "auto_repair": _loose_object(),
            "quality": _loose_object(),
            "base_resolution": {
                "enum": ["none", "provided", "canonical", "active_diagram"],
            },
            "present_request": _loose_object(),
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "preview_ref": _loose_object(),
            "render_request": _loose_object(),
            "next": _loose_object(),
        },
        required=("status", "source_ref", "spec_ref", "presentable"),
    ),
    "present_diagram": _object(
        {
            "status": {"enum": ["presented", "presented_with_delivery_warning"]},
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "preview_ref": _loose_object(),
            "review_request": _loose_object(),
            "delivery": _loose_object(),
            "next": _loose_object(),
        },
        required=(
            "status",
            "diagram_ref",
            "preview_ref",
            "review_request",
            "delivery",
            "next",
        ),
    ),
    "review_diagram": _object(
        {
            "status": {"const": "reviewed"},
            "review_id": _NON_EMPTY,
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "renders": {"type": "array", "items": _loose_object(), "minItems": 1, "maxItems": 4},
            "review_images": {"type": "array", "items": _loose_object(), "minItems": 1, "maxItems": 4},
            "visual_metrics": _loose_object(),
            "visual_issues": {"type": "array", "items": _loose_object()},
            "quality": _loose_object(),
            "agent_action_required": {"type": "boolean"},
            "agent_issues": {"type": "array", "items": _loose_object()},
            "render_hints": {"type": "array", "items": _loose_object()},
            "review_context": _loose_object(),
            "image_delivery": _loose_object(),
            "next": _loose_object(),
        },
        required=(
            "status",
            "review_id",
            "diagram_ref",
            "renders",
            "review_images",
            "visual_metrics",
            "visual_issues",
            "quality",
            "agent_action_required",
            "agent_issues",
            "render_hints",
            "review_context",
            "image_delivery",
            "next",
        ),
    ),
    "read_diagram_review_image": _object(
        {
            "status": {"const": "ok"},
            "sandbox_path": DIAGRAM_INPUT_SCHEMAS[
                "read_diagram_review_image"
            ]["properties"]["sandbox_path"],
            "mime_type": {"const": "image/png"},
            "bytes": {"type": "integer", "minimum": 1},
            "content_hash": deepcopy(_HASH),
            "instruction": _NON_EMPTY,
        },
        required=(
            "status",
            "sandbox_path",
            "mime_type",
            "bytes",
            "content_hash",
            "instruction",
        ),
    ),
    "export_diagram": _object(
        {
            "status": {"const": "exported"},
            "diagram_ref": deepcopy(DIAGRAM_REF),
            "export": _loose_object(),
            "download_ref": _loose_object(),
            "warnings": {"type": "array", "items": {"type": "string"}},
            "next": _loose_object(),
        },
        required=(
            "status",
            "diagram_ref",
            "export",
            "download_ref",
            "warnings",
            "next",
        ),
    ),
}


def diagram_input_schema(name: str) -> dict[str, Any] | None:
    schema = DIAGRAM_INPUT_SCHEMAS.get(name)
    return deepcopy(schema) if schema is not None else None


def diagram_output_schema(name: str) -> dict[str, Any] | None:
    schema = DIAGRAM_OUTPUT_SCHEMAS.get(name)
    return deepcopy(schema) if schema is not None else None

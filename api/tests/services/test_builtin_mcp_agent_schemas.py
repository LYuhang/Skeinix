"""Keep built-in MCP inputs simple enough for heterogeneous Agent models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vibecanvas_api.document_runtime.server import mcp as document_mcp
from vibecanvas_api.services.platform_mcp.invocation import platform_mcp_tool_manifest


PLATFORM_SERVERS = (
    "config",
    "interactive",
    "workflow",
    "task",
    "deployment",
    "knowledge",
    "build",
)
SCALAR_TYPES = {"boolean", "integer", "number", "string"}
INTERNAL_ARGUMENT_NAMES = {
    "capability",
    "chat_id",
    "organization_id",
    "runtime",
    "tenant_id",
    "turn_id",
    "user_id",
}


def _assert_agent_friendly_schema(*, tool_name: str, schema: dict[str, Any]) -> None:
    """Reject shapes that commonly produce invalid calls across providers."""
    assert schema.get("type") == "object", tool_name
    assert len(schema.get("required", [])) <= 4, tool_name
    properties = schema.get("properties", {})
    assert not (set(properties) & INTERNAL_ARGUMENT_NAMES), tool_name

    def inspect(value: Any, path: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                inspect(item, f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        assert "oneOf" not in value, f"{tool_name}: {path} uses oneOf"
        assert "allOf" not in value, f"{tool_name}: {path} uses allOf"
        assert "discriminator" not in value, f"{tool_name}: {path} uses discriminator"
        if "anyOf" in value:
            branches = value["anyOf"]
            branch_types = {branch.get("type") for branch in branches}
            assert branch_types <= (SCALAR_TYPES | {"array", "object", "null"}), (
                f"{tool_name}: {path} has a structured anyOf"
            )
            assert "null" in branch_types and len(branch_types) == 2, (
                f"{tool_name}: {path} has a non-nullable union"
            )
            for branch in branches:
                if branch.get("type") == "array":
                    assert branch.get("items", {}).get("type") in SCALAR_TYPES, (
                        f"{tool_name}: {path} has a structured array branch"
                    )
                if branch.get("type") == "object":
                    # A workflow input preset is naturally a free-form JSON
                    # mapping. It may remain one object leaf, but must not grow
                    # a model-facing nested protocol of its own.
                    assert branch.get("additionalProperties") is True, (
                        f"{tool_name}: {path} has a structured object branch"
                    )
                    assert "properties" not in branch, (
                        f"{tool_name}: {path} has nested object fields"
                    )
        for key, item in value.items():
            inspect(item, f"{path}.{key}")

    inspect(schema, "$")
    for name, prop in properties.items():
        prop_type = prop.get("type")
        assert prop_type != "object", f"{tool_name}: {name} is a nested object"
        if prop_type == "array":
            assert prop.get("items", {}).get("type") in SCALAR_TYPES, (
                f"{tool_name}: {name} is not a scalar array"
            )


def test_platform_mcp_input_schemas_are_flat_and_agent_friendly() -> None:
    for server in PLATFORM_SERVERS:
        for tool in platform_mcp_tool_manifest(server):
            _assert_agent_friendly_schema(
                tool_name=f"{server}/{tool.name}",
                schema=tool.inputSchema,
            )


def test_document_mcp_input_schemas_are_flat_and_agent_friendly() -> None:
    for tool in document_mcp._tool_manager.list_tools():
        _assert_agent_friendly_schema(
            tool_name=f"document/{tool.name}",
            schema=tool.parameters,
        )


def test_drawio_adapter_recommends_the_flat_file_preview_contract() -> None:
    root = Path(__file__).resolve().parents[3]
    source = (root / "api/drawio-runtime/launch.mjs").read_text(encoding="utf-8")
    publish_block = source.split("action: 'publish_after_visual_acceptance'", 1)[1]
    publish_block = publish_block.split("},\n          },", 1)[0]

    assert "file_type: 'drawio'" in publish_block
    assert "path," in publish_block
    assert "view:" not in publish_block
    assert "type: 'file_preview'" not in publish_block


def test_render_interactive_schema_has_no_union_or_nested_view() -> None:
    tool = next(
        tool
        for tool in platform_mcp_tool_manifest("interactive")
        if tool.name == "render_interactive"
    )
    serialized = json.dumps(tool.inputSchema)

    assert set(tool.inputSchema["properties"]) == {
        "path",
        "title",
        "file_type",
        "description",
        "require_human_confirm",
    }
    assert tool.inputSchema["required"] == ["path"]
    assert all(token not in serialized for token in ("oneOf", "anyOf", "discriminator", "view"))

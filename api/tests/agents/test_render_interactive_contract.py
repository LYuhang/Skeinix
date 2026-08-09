from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vibecanvas_api.agents.tools.decorator import ToolError
from vibecanvas_api.diagrams.registry import REGISTRY_VERSION, get_diagram_type
from vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive import (
    _persist_interactive_state,
    render_interactive,
)
from vibecanvas_api.services.platform_mcp.interactive_tools.schema import interactive_view_json_schema


def test_public_schema_has_only_html_and_file_views():
    schema = render_interactive.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {
        "title",
        "view",
        "require_human_confirm",
    }
    variants = schema["properties"]["view"]["oneOf"]
    assert {
        variant["properties"]["type"]["const"] for variant in variants
    } == {"html_preview", "file_preview"}
    assert '<form action="/data/<file>" method="post">' in render_interactive.description
    assert "fetch('/data/labels.json'" in render_interactive.description
    assert "platform-specific JavaScript object" in render_interactive.description
    assert "``/mount`` is read-only" in render_interactive.description
    assert "including paths constructed at" in render_interactive.description
    assert "Normal HTTP(S), ``data:``, and ``blob:`` URLs remain unchanged" in (
        render_interactive.description
    )
    assert "ordinary user-triggered ``fetch``" in render_interactive.description
    assert "do not overwrite the HTML" in render_interactive.description
    assert "Continue starts a" in render_interactive.description


@pytest.mark.asyncio
async def test_require_human_confirm_creates_continue_only_post_tool_gate(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)

    _, artifact = await render_interactive.coroutine(
        title="Review the result",
        view={"type": "html_preview", "html": "<p>Ready</p>"},
        require_human_confirm=True,
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                tenant_id="tenant_1",
                chat_id="chat_1",
                turn_id="turn_1",
            )
        ),
    )

    definition = artifact["payload"]["artifact"]
    assert definition["completion_mode"] == "wait_for_submit"
    assert definition["require_human_confirm"] is True
    assert definition["interaction_schema"] == {
        "interaction_type": "continue",
        "submit_label": "Continue",
    }
    persist.assert_awaited_once()


def test_generated_frontend_contract_matches_backend_schema():
    root = Path(__file__).resolve().parents[3]
    generated = json.loads(
        (root / "web/src/components/agent-sidebar/tool-render/interactive-view-schema.generated.json").read_text()
    )
    assert generated == interactive_view_json_schema()


@pytest.mark.asyncio
async def test_invalid_view_is_an_agent_readable_tool_error():
    content, artifact = await render_interactive.coroutine(
        title="Dataset review",
        view={"type": "slider", "min": 1, "max": 10},
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )
    assert "Fix these fields and call the tool again" in content
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "invalid_interactive_input"
    assert artifact["artifact"]["kind"] == "tool_error"


@pytest.mark.asyncio
async def test_successful_html_preserves_scripts_and_vfs_save_form(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )

    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)
    html = (
        "<form action='/data/labels.json' method='post'><img src='/data/items/1.png'>"
        "<input name='item-1.label'><button type='submit'>Save</button>"
        "<script>document.body.dataset.ready='1'</script></form>"
    )
    content, artifact = await render_interactive.coroutine(
        title="Dataset review",
        view={"type": "html_preview", "html": html},
        runtime=SimpleNamespace(
            context=SimpleNamespace(tenant_id="tenant_1", chat_id="chat_1", turn_id="turn_1")
        ),
    )
    definition = artifact["payload"]["artifact"]
    assert artifact["status"] == "success"
    assert definition["component_type"] == "html_preview"
    assert definition["props"]["html"] == html
    assert definition["completion_mode"] == "render_only"
    assert definition["interaction_schema"] == {}
    assert "render_interactive → html_preview" in content
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_does_not_return_a_nonrecoverable_card_without_chat_context():
    content, artifact = await render_interactive.coroutine(
        title="Dataset review",
        view={"type": "html_preview", "html": "<p>Review</p>"},
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )
    assert "durable chat context" in content
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "interactive_persistence_context_missing"


@pytest.mark.asyncio
async def test_database_failure_is_not_reported_as_a_successful_card(monkeypatch):
    from vibecanvas_api.storage import db

    def broken_session_scope(*, tenant_id: str):
        raise RuntimeError(f"database unavailable for {tenant_id}")

    monkeypatch.setattr(db, "session_scope", broken_session_scope)
    runtime = SimpleNamespace(
        context=SimpleNamespace(tenant_id="tenant_1", chat_id="chat_1", turn_id="turn_1")
    )

    with pytest.raises(ToolError, match="interactive_persistence_failed"):
        await _persist_interactive_state(
            runtime=runtime,
            artifact_id="ia_1",
            definition={
                "interaction_schema": {},
                "component_type": "html_preview",
                "completion_mode": "wait_for_submit",
            },
            component_type="html_preview",
            completion_mode="wait_for_submit",
            title="Dataset review",
            path=None,
            content_hash="sha256:test",
        )


def _valid_diagram_bytes() -> bytes:
    spec = get_diagram_type("flow", "basic")
    assert spec is not None
    return json.dumps({
        "schemaVersion": 1,
        "id": "interactive-flow",
        "title": "Interactive flow",
        "diagram": {"family": "flow", "type": "basic"},
        "model": {
            "nodes": [
                {"id": "start", "kind": "start", "label": "Start"},
                {"id": "done", "kind": "end", "label": "Done"},
            ],
            "edges": [{"id": "finish", "source": "start", "target": "done"}],
            "groups": [],
            "embeds": [],
            "resources": [],
        },
        "intent": {
            "direction": "RIGHT",
            "density": "comfortable",
            "stability": "preserve",
            "primaryPath": ["start", "done"],
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
    }).encode()


@pytest.mark.asyncio
async def test_invalid_diagram_file_creates_no_interactive_card(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)
    session = SimpleNamespace(
        read_file=AsyncMock(return_value={
            "ok": True,
            "kind": "text",
            "content": '{"schemaVersion":1}',
        }),
        sync_workspace_path=AsyncMock(return_value=True),
    )
    content, artifact = await render_interactive.coroutine(
        title="Broken flow",
        view={
            "type": "file_preview",
            "path": "/data/diagrams/broken.vdiagram.json",
        },
        runtime=SimpleNamespace(context=SimpleNamespace(_attached_session=session)),
    )

    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "invalid_diagram"
    assert "no Preview card was created" in content
    persist.assert_not_awaited()
    session.sync_workspace_path.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_diagram_file_is_checked_then_rendered_without_mutating_vfs(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)
    session = SimpleNamespace(
        read_file=AsyncMock(return_value={
            "ok": True,
            "kind": "text",
            "content": _valid_diagram_bytes().decode(),
        }),
        sync_workspace_path=AsyncMock(return_value=True),
    )
    _, artifact = await render_interactive.coroutine(
        title="Interactive flow",
        view={
            "type": "file_preview",
            "path": "/data/diagrams/flow.vdiagram.json",
        },
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                tenant_id="tenant_1",
                chat_id="chat_1",
                turn_id="turn_1",
                _attached_session=session,
            )
        ),
    )

    assert artifact["status"] == "success"
    definition = artifact["payload"]["artifact"]
    assert definition["props"]["mime"] == "application/vnd.vibecanvas.diagram+json"
    assert artifact["payload"]["diagram_validation"]["nodes"] == 2
    session.sync_workspace_path.assert_not_awaited()
    persist.assert_awaited_once()

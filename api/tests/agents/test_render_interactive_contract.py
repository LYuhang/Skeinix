from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vibecanvas_api.agents.tools.decorator import ToolError
from vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive import (
    _persist_interactive_state,
    render_interactive,
)
from vibecanvas_api.services.platform_mcp.interactive_tools.render_url_preview import (
    render_url_preview,
)
from vibecanvas_api.services.platform_mcp.interactive_tools.schema import interactive_view_json_schema


def test_public_schema_has_html_file_and_url_views():
    schema = render_interactive.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {
        "title",
        "view",
        "require_human_confirm",
    }
    variants = schema["properties"]["view"]["oneOf"]
    assert {
        variant["properties"]["type"]["const"] for variant in variants
    } == {"html_preview", "file_preview", "url_preview"}
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
async def test_url_preview_tool_publishes_isolated_webview_artifact(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)

    content, artifact = await render_url_preview.coroutine(
        title="Reference page",
        url="https://example.com/docs?section=preview",
        description="External documentation",
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                tenant_id="tenant_1",
                chat_id="chat_1",
                turn_id="turn_1",
            )
        ),
    )

    definition = artifact["payload"]["artifact"]
    assert artifact["status"] == "success"
    assert definition["component_type"] == "url_preview"
    assert definition["props"] == {
        "url": "https://example.com/docs?section=preview",
        "description": "External documentation",
    }
    assert definition["height"] == 520
    assert "render_interactive → url_preview" in content
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_url_preview_rejects_non_http_navigation(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)

    content, artifact = await render_url_preview.coroutine(
        title="Unsafe page",
        url="javascript:alert(1)",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "invalid_interactive_input"
    assert "absolute HTTP(S) URL" in content
    persist.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_file_preview_defers_type_validation_to_preview(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)
    session = SimpleNamespace(sync_workspace_path=AsyncMock(return_value=True))
    content, artifact = await render_interactive.coroutine(
        title="Broken flow",
        view={
            "type": "file_preview",
            "path": "/data/diagrams/broken.drawio",
        },
        runtime=SimpleNamespace(context=SimpleNamespace(_attached_session=session)),
    )

    assert artifact["status"] == "success"
    assert "render_interactive → file_preview" in content
    assert artifact["payload"]["artifact"]["props"] == {
        "path": "/data/diagrams/broken.drawio",
        "file_type": "auto",
        "description": "",
    }
    persist.assert_awaited_once()
    session.sync_workspace_path.assert_awaited_once_with(
        "/data/diagrams/broken.drawio"
    )


@pytest.mark.asyncio
async def test_file_preview_preserves_optional_type_hint(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)
    session = SimpleNamespace(sync_workspace_path=AsyncMock(return_value=True))
    _, artifact = await render_interactive.coroutine(
        title="Interactive flow",
        view={
            "type": "file_preview",
            "path": "/data/diagrams/flow.drawio",
            "file_type": "drawio",
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
    assert definition["props"]["file_type"] == "drawio"
    assert "diagram_validation" not in artifact["payload"]
    session.sync_workspace_path.assert_awaited_once_with(
        "/data/diagrams/flow.drawio"
    )
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsynced_diagram_file_creates_no_interactive_card(monkeypatch):
    module = importlib.import_module(
        "vibecanvas_api.services.platform_mcp.interactive_tools.render_interactive"
    )
    persist = AsyncMock()
    monkeypatch.setattr(module, "_persist_interactive_state", persist)
    session = SimpleNamespace(sync_workspace_path=AsyncMock(return_value=False))

    content, artifact = await render_interactive.coroutine(
        title="Unsynced flow",
        view={
            "type": "file_preview",
            "path": "/data/diagrams/unsynced.drawio",
        },
        runtime=SimpleNamespace(context=SimpleNamespace(_attached_session=session)),
    )

    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "file_preview_sync_failed"
    assert "before creating its Preview" in content
    persist.assert_not_awaited()

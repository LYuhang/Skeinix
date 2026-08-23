"""Platform MCP ``render_interactive`` — durable HTML and file previews."""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools._session_fs import _require_session
from vibecanvas_api.agents.tools._envelope import tool_ok
from vibecanvas_api.agents.tools.decorator import (
    ToolError,
    _approx_tokens,
    _head_tail_preview,
    _offload_from_runtime,
    _serialize,
    tool_error_boundary,
)
from vibecanvas_api.services.platform_mcp.interactive_tools.schema import (
    ViewArgument,
    validate_view,
)
from vibecanvas_api.config import config

logger = logging.getLogger(__name__)

INTERACTIVE_CONTENT_TYPE = "application/vnd.vibecanvas.interactive-artifact+json"

def _default_height(component_type: str) -> int:
    if component_type == "url_preview":
        return 520
    return 360 if component_type == "html_preview" else 320


def _default_preview(component_type: str) -> dict[str, str]:
    # Layout is a client capability, not an Agent decision. Main chat exposes
    # expansion for HTML/file content; the browser side panel stays inline.
    return {
        "mode": "optional"
        if component_type in {"html_preview", "file_preview", "url_preview"}
        else "none"
    }


def _preview_payload(full: dict[str, Any], preview_chars: int) -> dict[str, Any]:
    text = _serialize(full)
    return {
        "artifact_id": full.get("artifact_id"),
        "title": full.get("title"),
        "component_type": full.get("component_type"),
        "preview": _head_tail_preview(text, preview_chars),
    }


async def _prepare_file_preview(*, runtime: ToolRuntime, path: str) -> None:
    """Persist a generated file before publishing its path to Preview."""
    if not path.startswith("/data/"):
        return
    session = await _require_session(runtime.context)
    if not await session.sync_workspace_path(path):
        raise ToolError(
            "file_preview_sync_failed",
            f"Unable to persist {path} before creating its Preview.",
            info={"path": path},
        )


@tool(response_format="content_and_artifact")
@tool_error_boundary(tool="render_interactive")
async def render_interactive(
    title: str,
    view: ViewArgument,
    require_human_confirm: bool = False,
    *,
    runtime: ToolRuntime,
) -> tuple[str, dict[str, Any]]:
    """Show a durable rich-content card in the conversation.

    Use this instead of a long text response when a rich visual, interactive
    control, or file preview communicates the result better. The public surface
    intentionally has three view types:

    - ``html_preview`` for any custom UI. It accepts self-contained HTML with
      inline JavaScript/CSS and local VFS/data/blob resources, and can render
      images, tables, charts, Canvas/SVG, sliders, forms, and other dynamic
      presentations in an isolated iframe. External code and network
      subresources are blocked; save any required remote asset into VFS first.
    - ``file_preview`` for any file that already exists in your local
      environment. Supply the file path and optional description. ``file_type``
      defaults to ``auto``; set it only when the file has no reliable extension
      or MIME metadata. The Preview service interprets the hint and selects a
      renderer.
    - ``url_preview`` for an ordinary HTTP(S) page. The browser opens the URL
      in an isolated interactive WebView without Skeinix authentication data.
      There is no destination allowlist, but sites may refuse iframe embedding
      through their own browser security headers.

    ``view`` is a strict object selected by ``view.type``. Valid examples:

    - Display an image created earlier:
      ``{"type":"html_preview","html":"<img src='/data/shot.png' alt='Screenshot'>"}``
    - Render a dataset dynamically:
      ``{"type":"html_preview","html":"<div id='grid'></div><script>for(let i=1;i<=8;i++){const img=document.createElement('img');img.src=`/data/dataset/images/${i}.png`;document.querySelector('#grid').append(img)}</script>"}``
    - Collect and save user input with ordinary HTML and JavaScript. For
      example, a user-triggered Save button can write structured data to an
      Agent-selected path:
      ``save.onclick=()=>fetch('/data/labels.json', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(labels)})``.
      A standard named form with ``action="/data/labels.json"`` also saves its
      current values as JSON when the user submits it.
    - Preview an existing file:
      ``{"type":"file_preview","path":"/data/report.pdf","description":"Generated report"}``
    - Preview a mounted file without copying its contents into the call:
      ``{"type":"file_preview","path":"/mount/data/report.docx"}``
    - Open a web page in Preview:
      ``{"type":"url_preview","url":"https://example.com/docs","description":"Reference"}``

    Agent contract for HTML:

    1. Write an ordinary, self-contained web page. Do not use or invent a
       Skeinix platform SDK.
    2. Read local files using their normal absolute paths in ``src``, ``href``,
       CSS URLs, dynamic DOM assignments, or ``fetch``. The rendering runtime
       resolves those paths transparently, including paths constructed at
       runtime from a statically declared directory prefix. Normal HTTP(S), ``data:``, and ``blob:`` URLs remain unchanged by local-path rewriting;
       however, the isolated iframe may block remote network access, so download
       required remote assets into VFS before referencing them.
    3. For editable UI, either attach a normal Save-button handler that calls
       ``fetch`` or use named form controls and
       ``<form action="/data/<file>" method="post">``. Both save to VFS only;
       neither action continues the Agent conversation.
    4. For custom save logic, use an ordinary user-triggered ``fetch`` with
       ``PUT`` or ``POST`` to ``/data/<file>`` and a text or JSON body. Treat
       the returned ``Response`` like a normal fetch response.
    5. ``/data`` is writable after a real user interaction; ``/mount`` is
       read-only. Do not write during page load or from timers without a recent
       user action.
    6. Keep the HTML definition separate from submitted data. Save labels,
       annotations, or other results to a data file; do not overwrite the HTML
       source to persist UI state.

    Set ``require_human_confirm=true`` when the Agent must stop after rendering
    and wait for the user to click Continue below the card. Continue starts a
    new Human Turn; the platform does not suspend or resume the tool execution
    stack. Omit it for display-only content and the Agent Turn continues.

    Layout and optional larger preview presentation are selected automatically.
    HTML runs with inline scripts enabled in an isolated rendering environment.
    Local resources may use the same absolute file paths that are available to
    you, such as ``/data/shot.png``;
    no URL conversion, user/Chat identifier, authentication token, or
    platform-specific JavaScript object is needed. Writes are allowed under
    ``/data`` after a user interaction; ``/mount`` is read-only. Use standard
    named HTML form controls for durable user input. Do not access the parent
    page or authentication state.

    Invalid fields return an ``invalid_interactive_input`` tool error containing
    precise field paths. Correct the input and call this tool again.
    """
    cfg = config.agent.compaction_v2
    title_clean = (title or "").strip()
    if not title_clean:
        raise ToolError(
            "invalid_interactive_input",
            "Invalid render_interactive title: provide a short, user-facing title and call the tool again.",
            info={"field": "title"},
        )
    view_obj = validate_view(view)
    component_type = view_obj.type
    props_obj = view_obj.model_dump(exclude={"type"}, exclude_none=True, mode="json")
    if component_type == "file_preview":
        preview_path = str(props_obj.get("path") or "")
        await _prepare_file_preview(
            runtime=runtime,
            path=preview_path,
        )
    schema_obj = (
        {
            "interaction_type": "continue",
            "submit_label": "Continue",
        }
        if require_human_confirm
        else {}
    )
    completion_mode = (
        "wait_for_submit"
        if require_human_confirm
        else "render_only"
    )
    artifact_id = f"ia_{uuid.uuid4().hex[:12]}"

    definition: dict[str, Any] = {
        "kind": "interactive_artifact",
        "schema_version": 1,
        "artifact_id": artifact_id,
        "title": title_clean,
        "component_type": component_type,
        "props": props_obj,
        "interaction_schema": schema_obj,
        "completion_mode": completion_mode,
        "require_human_confirm": bool(require_human_confirm),
        "height": _default_height(component_type),
        "preview": _default_preview(component_type),
        "widget_state": {},
        # The outer Agent Loop creates and binds the post-tool HITL request
        # after it observes this durable ToolMessage. Tools describe content;
        # they do not own pause/resume control flow.
        "hitl_request_id": None,
        "interaction_state": {
            "is_interacted": False,
            # This value is never used to drive control flow. For a Continue
            # gate the outer Agent Loop replaces it with ``pending`` only
            # after the durable HITL row and checkpoint reference both exist.
            "status": (
                "awaiting_loop_gate"
                if completion_mode == "wait_for_submit"
                else "none"
            ),
            "result": {},
        },
    }

    serialized = _serialize(definition)
    content_hash = hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()
    inline_limit = int(cfg.interactive_artifact_inline_chars)
    preview_chars = int(cfg.interactive_artifact_offload_preview_chars)
    path: str | None = None
    inline_definition: dict[str, Any] | None = definition

    if len(serialized) > inline_limit:
        offload = _offload_from_runtime(
            runtime,
            base_dir=cfg.interactive_artifact_offload_dir,
        )
        if offload is not None:
            path = offload(serialized, INTERACTIVE_CONTENT_TYPE)
            if path:
                inline_definition = None

    output: dict[str, Any] = {
        "content_type": INTERACTIVE_CONTENT_TYPE,
        "data": inline_definition if inline_definition is not None else _preview_payload(definition, preview_chars),
        "artifact_id": artifact_id,
        "component_type": component_type,
        "completion_mode": completion_mode,
        "full_chars": len(serialized),
        "full_tokens": _approx_tokens(serialized),
        "hash": f"sha256:{content_hash}",
    }
    if path:
        output["path"] = path

    abstract = f"render_interactive → {component_type}: {title_clean}"
    content = tool_ok(abstract, output)
    artifact = {
        "schema_version": 1,
        "status": "success",
        "error": None,
        "content": content,
        "content_abstract": abstract,
        "ref": path or f"tool://render_interactive/{content_hash[:12]}",
        "artifact": {
            "kind": "interactive_artifact",
            "target": {"path": path} if path else {},
        },
        "payload": {
            "kind": "interactive_artifact",
            "content_type": INTERACTIVE_CONTENT_TYPE,
            "artifact": inline_definition,
            "artifact_preview": None if inline_definition is not None else output["data"],
            "artifact_ref": path,
            "hitl_request_id": None,
            "hash": f"sha256:{content_hash}",
            "size": {"chars": len(serialized), "tokens": _approx_tokens(serialized)},
        },
        "meta": {
            "tool": "render_interactive",
            "content_type": INTERACTIVE_CONTENT_TYPE,
            "stale_on_reread": False,
            "tokens": {
                "content": _approx_tokens(content),
                "content_abstract": _approx_tokens(abstract),
                "ref": _approx_tokens(path or ""),
            },
            "content_hash": f"sha256:{content_hash}",
            "protect_recent_rounds": cfg.interactive_artifact_protect_recent_rounds,
        },
    }
    await _persist_interactive_state(
        runtime=runtime,
        artifact_id=artifact_id,
        definition=definition,
        component_type=component_type,
        completion_mode=completion_mode,
        title=title_clean,
        path=path,
        content_hash=f"sha256:{content_hash}",
    )
    return content, artifact


async def _persist_interactive_state(
    *,
    runtime: ToolRuntime,
    artifact_id: str,
    definition: dict[str, Any],
    component_type: str,
    completion_mode: str,
    title: str,
    path: str | None,
    content_hash: str,
) -> None:
    ctx = runtime.context
    tenant_id = getattr(ctx, "tenant_id", None)
    chat_id = getattr(ctx, "chat_id", None)
    run_id = getattr(ctx, "turn_id", None)
    if not tenant_id or not chat_id:
        raise ToolError(
            "interactive_persistence_context_missing",
            "Interactive content requires a durable chat context and was not rendered.",
        )
    try:
        from vibecanvas_api.storage.db import session_scope
        from vibecanvas_api.storage.hitl_repo import HitlRepo

        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = HitlRepo(session)
            await repo.create_interactive_artifact(
                artifact_id=artifact_id,
                tenant_id=str(tenant_id),
                chat_id=str(chat_id),
                run_id=str(run_id) if run_id else None,
                component_type=component_type,
                completion_mode=completion_mode,
                title=title,
                definition_json=definition,
                artifact_ref=path,
                content_hash=content_hash,
                hitl_request_id=None,
            )
            # The outer Loop may observe this ToolMessage immediately after the
            # tool returns, so the artifact fact must already be committed.
            await repo.commit()
    except ToolError:
        raise
    except Exception as exc:
        logger.warning("render_interactive_persist_failed", exc_info=True)
        raise ToolError(
            "interactive_persistence_failed",
            "The interactive content could not be saved, so no non-recoverable card was shown.",
        ) from exc

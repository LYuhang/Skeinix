"""Runtime debug snapshot writer for model-facing agent context.

This edit is observational: it runs at the end of the ContextEditingMiddleware
edit chain, serializes the already-projected model-facing messages, and writes a
debug JSON file to the chat workspace VFS. It never mutates messages and never
blocks a model call.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vibecanvas_api.storage.sync_session import current_sync_tenant_id

logger = structlog.get_logger(__name__)

DEBUG_DIR = "/logs/.debug"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _role(msg: Any) -> str:
    if isinstance(msg, HumanMessage):
        return "user"
    if isinstance(msg, AIMessage):
        return "assistant"
    if isinstance(msg, ToolMessage):
        return "tool"
    if isinstance(msg, SystemMessage):
        return "system"
    return "unknown"


def _content_text(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _meta(msg: Any) -> dict:
    out: dict[str, Any] = {}
    ak = getattr(msg, "additional_kwargs", None)
    if isinstance(ak, dict) and isinstance(ak.get("_meta"), dict):
        out.update(ak["_meta"])
    for slot in ("additional_kwargs", "response_metadata"):
        value = getattr(msg, slot, None)
        if isinstance(value, dict) and isinstance(value.get("tokens"), dict):
            out["tokens"] = value["tokens"]
            break
    return out


def _token_slots(meta: dict) -> dict:
    tokens = meta.get("tokens") if isinstance(meta.get("tokens"), dict) else {}
    out: dict[str, int | None] = {
        "raw": tokens.get("content") or tokens.get("raw"),
        "preview": tokens.get("content_abbreviation") or tokens.get("head_tail"),
        "abstract": tokens.get("content_abstract") or tokens.get("abstract"),
        "ref": tokens.get("ref"),
        "compressed": tokens.get("content_compress") or tokens.get("compressed"),
    }
    return out


def _form_from_meta(meta: dict) -> tuple[str, str | None, str]:
    tokens = meta.get("tokens") if isinstance(meta.get("tokens"), dict) else {}
    # ``TokenRecordMiddleware`` and ``LifecyclePolicyEdit`` persist the canonical
    # token-accounting schema as ``form`` + ``raw/head_tail/abstract/...``.
    # Form-ladder v2 still exposes ``current_form`` + ``content/...`` internally,
    # so normalize either producer at this observational boundary.  In
    # particular, do not default a live ``form=head_tail`` message back to raw:
    # that would make the inspector count the original tool payload instead of
    # the already-projected model input.
    raw = tokens.get("form") or tokens.get("current_form") or "raw"
    if raw in {"content", "raw"}:
        return "raw", None, "raw"
    if raw in {"content_abbreviation", "head_tail"}:
        return "preview", "head_tail", "preview"
    if raw in {"content_abstract", "abstract", "minimal", "reference"}:
        return "abstract", None, "abstract"
    if raw == "ref":
        return "ref", None, "ref"
    if raw in {"content_compress", "compressed"}:
        return "compressed", None, "compressed"
    return str(raw), None, str(raw)


def _artifact_meta(msg: Any) -> tuple[str | None, str | None, bool]:
    artifact = getattr(msg, "artifact", None)
    if not isinstance(artifact, dict):
        return None, None, False
    ameta = artifact.get("meta") if isinstance(artifact.get("meta"), dict) else {}
    payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
    art = artifact.get("artifact") if isinstance(artifact.get("artifact"), dict) else {}
    target = art.get("target") if isinstance(art.get("target"), dict) else {}
    path = payload.get("ref") or target.get("path")
    content_type = ameta.get("content_type")
    status = artifact.get("status")
    return (
        path if isinstance(path, str) else None,
        content_type if isinstance(content_type, str) else None,
        status == "error",
    )


def _synthetic_kind(content: str) -> str | None:
    if "<hard-context>" in content:
        return "hard_context"
    if "<agent-state>" in content:
        return "state_memory"
    if "<background-tasks>" in content:
        return "background_task"
    if "<todo-reminder>" in content or "Current todo list:" in content:
        return "todo_reminder"
    if "<system-reminder>" in content:
        return "system_reminder"
    return None


def _serialize_message(msg: Any, index: int, *, anchor_id: str | None,
                       count_tokens: Any) -> tuple[dict, str | None]:
    content = _content_text(msg)
    kind = _synthetic_kind(content)
    mid = getattr(msg, "id", None)
    source_id = str(mid) if mid and not kind else None
    meta = _meta(msg)
    form, preview_strategy, token_field = _form_from_meta(meta)
    slots = _token_slots(meta)
    tokens = slots.get(token_field)
    if tokens is None:
        try:
            tokens = int(count_tokens([msg]))
        except Exception:
            tokens = None
    path, content_type, artifact_error = _artifact_meta(msg)
    item: dict[str, Any] = {
        "debug_id": f"dbg_msg_{index:04d}",
        "source_message_id": source_id,
        "role": _role(msg),
        "synthetic": bool(kind),
        "form": form,
        "token_field": token_field,
        "tokens": tokens,
        "token_slots": slots,
        "content": content,
    }
    if preview_strategy:
        item["preview_strategy"] = preview_strategy
    if kind:
        item["synthetic_kind"] = kind
        if anchor_id:
            item["anchor_source_message_id"] = anchor_id
    if isinstance(msg, ToolMessage):
        tcid = getattr(msg, "tool_call_id", None)
        if tcid:
            item["tool_call_id"] = tcid
        name = getattr(msg, "name", None)
        if name:
            item["tool_name"] = name
    if isinstance(msg, AIMessage):
        calls = getattr(msg, "tool_calls", None) or []
        if calls:
            item["tool_calls"] = calls
    if path:
        item["path"] = path
    if content_type:
        item["content_type"] = content_type
    if artifact_error:
        item["error"] = True
    next_anchor = source_id or anchor_id
    return item, next_anchor


def _write_snapshot(vfs: Any, tenant_id: str, workspace_scope_id: str, path: str,
                    payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if os.environ.get("VIBECANVAS_AGENT_RUNTIME_IN_SANDBOX") == "1":
        # /logs is the Runtime's writable workspace mount. Writing there keeps
        # the sandbox independent from the host object-store path; the owning
        # SandboxSession performs the normal VFS writeback after the Turn.
        local_path = os.path.abspath(path)
        if not (
            local_path == DEBUG_DIR
            or local_path.startswith(DEBUG_DIR.rstrip("/") + "/")
        ):
            raise ValueError("debug snapshot path must stay under /logs/.debug")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w", encoding="utf-8") as file:
            file.write(body)
        return

    token = current_sync_tenant_id.set(tenant_id)
    try:
        if hasattr(vfs, "upsert_internal_artifact"):
            vfs.upsert_internal_artifact(
                wf_id=workspace_scope_id,
                path=path,
                content=body,
                content_type="application/json",
                abstract="Agent model-input debug snapshot",
            )
        else:
            vfs.write_artifact(
                wf_id=workspace_scope_id,
                category="logs/.debug",
                basename=payload["snapshot_id"],
                content=body,
                content_type="application/json",
                abstract="Agent model-input debug snapshot",
            )
    finally:
        current_sync_tenant_id.reset(token)


@dataclass
class DebugSnapshotEdit:
    """Write one debug snapshot for each model call when enabled."""

    context: Any
    vfs: Any
    agent_cfg: Any
    call_index: int = 0
    _tasks: list[asyncio.Task] = field(default_factory=list)

    def apply(self, messages: list, *, count_tokens: Any) -> None:
        try:
            ctx = self.context.get("context") if isinstance(self.context, dict) else self.context
            if ctx is None or self.vfs is None:
                return
            tenant_id = str(getattr(ctx, "tenant_id", "") or "")
            workspace_scope_id = str(getattr(ctx, "wf_id", "") or "")
            chat_id = str(getattr(ctx, "chat_id", "") or "")
            if not tenant_id or not workspace_scope_id or not chat_id:
                return

            self.call_index += 1
            call_index = self.call_index
            stamp = _utc_stamp()
            snapshot_id = f"{stamp}__turn_{chat_id}__call_{call_index:03d}"
            path = f"{DEBUG_DIR}/{snapshot_id}.json"
            items: list[dict] = []
            anchor_id: str | None = None
            token_total = 0
            for idx, msg in enumerate(messages, start=1):
                item, anchor_id = _serialize_message(
                    msg, idx, anchor_id=anchor_id, count_tokens=count_tokens)
                if isinstance(item.get("tokens"), int):
                    token_total += int(item["tokens"])
                items.append(item)

            compaction_v2 = _cfg_value(self.agent_cfg, "compaction_v2")
            if isinstance(compaction_v2, dict):
                context_window_tokens = compaction_v2.get("window_tokens")
                compaction_enabled = bool(compaction_v2.get("v2_enabled", False))
            else:
                context_window_tokens = getattr(compaction_v2, "window_tokens", None)
                compaction_enabled = bool(getattr(compaction_v2, "v2_enabled", False))
            model_id = str(_cfg_value(self.agent_cfg, "model", "") or "")
            context_manifest = getattr(ctx, "context_manifest", {})
            context_manifest = (
                dict(context_manifest) if isinstance(context_manifest, dict) else {}
            )
            target = {
                "provider": model_id.split(":", 1)[0] if ":" in model_id else "",
                "model_id": model_id,
                "context_window_tokens": (
                    _cfg_value(self.agent_cfg, "model_context_tokens")
                    or context_window_tokens
                ),
            }
            payload = {
                "schema_version": 1,
                "kind": "agent_model_input_snapshot",
                "runtime_type": "langchain",
                "snapshot_semantics": "model_input",
                "snapshot_id": snapshot_id,
                "chat_id": chat_id,
                "thread_id": str(getattr(ctx, "thread_id", "") or ""),
                "turn_id": str(getattr(ctx, "turn_id", "") or ""),
                "model_call_index": call_index,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "target": target,
                "memory_config_snapshot": {
                    "compaction_v2_enabled": compaction_enabled,
                    "context_manifest_mode": (
                        context_manifest.get("mode")
                    ),
                },
                "token_total": token_total,
                "messages": items,
                "context_manifest": (
                    context_manifest
                ),
                "context_decisions": list(
                    (self.context.get("context_decisions") or [])
                    if isinstance(self.context, dict)
                    else []
                ),
                "context_compaction_plan": (
                    dict(self.context.get("context_compaction_plan") or {})
                    if isinstance(self.context, dict)
                    else {}
                ),
                "tool_registry": list(
                    (self.context.get("tool_registry") or [])
                    if isinstance(self.context, dict)
                    else []
                ),
                "mcp_catalog": list(
                    (self.context.get("mcp_catalog") or [])
                    if isinstance(self.context, dict)
                    else []
                ),
                "runtime_policy": {
                    "approval_mode": str(getattr(ctx, "approval_mode", "agent")),
                    "session_memory_scope": "chat_checkpoint",
                    "long_term_memory_enabled": False,
                    "limits": (
                        dict(self.context.get("runtime_limits") or {})
                        if isinstance(self.context, dict)
                        else {}
                    ),
                    "mcp": [{
                        "name": item.get("name"),
                        "health": item.get("health"),
                        "cache_status": item.get("cache_status"),
                        "handshake_ms": item.get("handshake_ms"),
                        "retry_count": item.get("retry_count"),
                        "error": item.get("error"),
                    } for item in (
                        (self.context.get("mcp_catalog") or [])
                        if isinstance(self.context, dict)
                        else []
                    ) if isinstance(item, dict)],
                },
            }

            task = asyncio.create_task(asyncio.to_thread(
                _write_snapshot, self.vfs, tenant_id, workspace_scope_id, path, payload))
            task.add_done_callback(self._log_task_result)
            self._tasks.append(task)
            if len(self._tasks) > 16:
                self._tasks = [t for t in self._tasks if not t.done()]
        except Exception:
            logger.warning("agent_debug_snapshot_schedule_failed", exc_info=True)

    @staticmethod
    def _log_task_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception:
            logger.warning("agent_debug_snapshot_write_failed", exc_info=True)

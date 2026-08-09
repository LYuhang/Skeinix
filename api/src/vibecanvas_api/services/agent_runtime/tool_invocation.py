"""Stable Runtime-neutral tool invocation product envelope."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"password|passwd|token|cookie|authorization|secret|credential|api[_-]?key",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if depth > 12:
        return "[nested value omitted]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact(child, key=str(child_key), depth=depth + 1)
            for child_key, child in list(value.items())[:200]
        }
    if isinstance(value, (list, tuple)):
        return [_redact(child, key=key, depth=depth + 1) for child in value[:200]]
    return value


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return _redact(json.loads(value))
    except Exception:
        return value


def _classification(
    name: str,
    *,
    runtime_type: str,
    mcp_catalog: list[dict[str, Any]] | None,
    native_kind: str | None,
) -> tuple[dict[str, Any], str, str]:
    if native_kind == "commandExecution" or name in {"bash", "shell"}:
        return ({
            "kind": "runtime_native" if native_kind else "builtin",
            "provider": runtime_type,
        }, "terminal", "execute")
    if native_kind == "fileChange" or name in {"write_file", "edit_file", "file_change"}:
        return ({
            "kind": "runtime_native" if native_kind else "builtin",
            "provider": runtime_type,
        }, "file.diff", "write")
    for server in mcp_catalog or []:
        if not isinstance(server, dict):
            continue
        exported_names = {
            str(item.get("name") or "")
            for item in server.get("tools") or []
            if isinstance(item, dict)
        }
        if name in exported_names or name.startswith(f"{server.get('name')}__"):
            source = str(server.get("source") or "custom")
            server_name = str(server.get("name") or "mcp")
            raw_tool_name = name.split("__", 1)[-1] if "__" in name else name
            tool_descriptor = next(
                (
                    item for item in server.get("tools") or []
                    if isinstance(item, dict)
                    and str(item.get("name") or "") in {name, raw_tool_name}
                ),
                {},
            )
            return ({
                "kind": "platform_mcp" if source == "platform" else "custom_mcp",
                "serverId": server.get("server_id") or server.get("id"),
                "serverName": server_name,
                "serverLabel": server.get("label") or server_name,
                "toolName": raw_tool_name,
                "qualifiedName": name,
            }, str(tool_descriptor.get("capability") or server_name), "unknown")
    if native_kind == "mcpToolCall":
        server_name = name.split("__", 1)[0]
        return ({
            "kind": "unknown",
            "provider": "mcp",
            "serverName": server_name,
            "toolName": name.split("__", 1)[-1],
            "qualifiedName": name,
        }, "generic", "unknown")
    if native_kind == "dynamicToolCall":
        return ({"kind": "dynamic", "provider": runtime_type}, "generic", "unknown")
    if name in {"read_file", "grep", "read_images"}:
        return ({"kind": "builtin", "provider": runtime_type}, "file.read", "read")
    if name.startswith("background_job_"):
        return ({"kind": "builtin", "provider": runtime_type}, "background.job", "read")
    return ({"kind": "builtin", "provider": runtime_type}, "generic", "unknown")


def start_tool_invocation(
    *,
    invocation_id: str,
    runtime_type: str,
    name: str,
    arguments: Any,
    mcp_catalog: list[dict[str, Any]] | None = None,
    native_kind: str | None = None,
) -> tuple[dict[str, Any], float]:
    origin, capability, risk = _classification(
        name,
        runtime_type=runtime_type,
        mcp_catalog=mcp_catalog,
        native_kind=native_kind,
    )
    envelope = {
        "schemaVersion": 1,
        "invocationId": invocation_id,
        "runtime": {"type": runtime_type},
        "origin": origin,
        "capability": capability,
        "name": name,
        "status": "running",
        "input": _redact(_json_value(arguments)),
        "risk": risk,
        "timing": {"startedAt": _utc_now()},
    }
    if native_kind:
        envelope["nativeKind"] = native_kind
    return envelope, time.perf_counter()


def finish_tool_invocation(
    started: dict[str, Any] | None,
    *,
    started_monotonic: float | None,
    invocation_id: str,
    runtime_type: str,
    name: str,
    status: str,
    content: str,
    artifact: dict[str, Any] | None,
    mcp_catalog: list[dict[str, Any]] | None = None,
    native_kind: str | None = None,
) -> dict[str, Any]:
    if started is None:
        started, started_monotonic = start_tool_invocation(
            invocation_id=invocation_id,
            runtime_type=runtime_type,
            name=name,
            arguments={},
            mcp_catalog=mcp_catalog,
            native_kind=native_kind,
        )
    done = dict(started)
    timing = dict(done.get("timing") or {})
    timing["endedAt"] = _utc_now()
    if started_monotonic is not None:
        timing["durationMs"] = max(
            0, int((time.perf_counter() - started_monotonic) * 1000)
        )
    normalized_status = (
        "error" if status in {"error", "failed", "errored"}
        else "cancelled" if status in {"cancelled", "canceled", "declined"}
        else "success"
    )
    meta = (
        artifact.get("meta")
        if isinstance(artifact, dict) and isinstance(artifact.get("meta"), dict)
        else {}
    )
    done.update({
        "status": normalized_status,
        "output": {
            "content": [{"type": "text", "text": content}],
            "structuredContent": artifact,
            "isError": normalized_status == "error",
        },
        "presentation": {
            "contentType": meta.get("content_type"),
            "kind": meta.get("presentation_kind"),
        },
        "timing": timing,
    })
    if normalized_status == "error":
        done["error"] = {"message": content[:2000] or "Tool execution failed"}
    return done

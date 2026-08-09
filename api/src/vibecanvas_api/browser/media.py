"""Browser media bytes → chat workspace VFS path at the host (§5.3).

The plugin streams bytes once; the host writes them into
the current chat workspace's existing `/data` mount as
`/data/browser-media/<hash>.<ext>` (content-hash dedup) and rewrites the
observation so downstream carries ONLY paths — never bytes.

Important boundary: browser side-panel media is chat-local workspace data, not
workflow execution data. Do not store it under `/run`; `/run` is reserved for
workflow-run files.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import re
from typing import Callable

from ..storage.vfs_store import PostgresVfsStore
from .commands import Observation

_BAD_SEGMENT = re.compile(r"(^|/)(?:\.|\.\.)(?:/|$)")


def normalize_browser_media_save_path(save_path: str | None) -> str | None:
    path = (save_path or "").strip()
    if not path:
        return None
    if not path.startswith("/data/") or path.endswith("/") or "\x00" in path or _BAD_SEGMENT.search(path):
        raise ValueError(
            "browser media save_path must be an exact file path under the current chat workspace /data/ folder"
        )
    return path


def write_observation_media(
    obs: Observation,
    *,
    workspace_scope_id: str,
    tenant_id: str,
    save_path: str | None = None,
) -> Observation:
    if not obs.media:
        return obs
    store = PostgresVfsStore()
    requested_path = normalize_browser_media_save_path(save_path)
    seen: dict[str, str] = {}                 # hash -> path (dedup within one obs)
    by_slot: dict[str, list[str]] = {}
    reduced: list[dict] = []
    media_items = list(obs.media)
    for m in media_items:
        data = base64.b64decode(str(m["b64"]))
        h = hashlib.sha256(data).hexdigest()
        ext = str(m.get("ext", "bin"))
        path = requested_path if requested_path and len(media_items) == 1 else f"/data/browser-media/{h}.{ext}"
        if h not in seen:
            # The object store is flat (no directory to pre-create), but the
            # metadata write can still fail (RLS/tenant, blob backend, quota).
            # Surface a clear agent-readable error instead of a cryptic store
            # traceback.
            try:
                store.upsert_artifact_bytes(
                    wf_id=workspace_scope_id,
                    path=path,
                    data=data,
                    content_type=str(m.get("mime", "application/octet-stream")),
                    abstract=f"Browser media slot {m.get('slot') or 'media'}",
                )
            except Exception as e:  # noqa: BLE001 — re-raised with context
                raise RuntimeError(
                    f"captured {len(data)} bytes for slot {m.get('slot')!r} but "
                    f"failed to save them to the chat workspace at {path}: {e}"
                ) from e
            seen[h] = path
        by_slot.setdefault(str(m["slot"]), []).append(seen[h])
        reduced.append({
            "slot": m["slot"],
            "path": seen[h],
            "bytes_len": len(data),
            "mime": str(m.get("mime", "application/octet-stream")),
        })
    new_data = dict(obs.data)
    for slot, paths in by_slot.items():
        new_data[slot] = paths if len(paths) > 1 else paths[0]
    new_data["media"] = reduced
    return dataclasses.replace(obs, data=new_data, media=reduced)


def host_media_writer(workspace_scope_id: str, tenant_id: str) -> Callable[..., Observation]:
    """Per-producer media resolver.

    The browser tool layer binds the current chat workspace scope before calling
    command_host.send_command; CommandHost invokes this writer after receiving an
    observation and before returning it to the tool.
    """
    def _writer(
        obs: Observation,
        *,
        transport_id: str | None = None,
        cmd=None,
        args: dict | None = None,
    ) -> Observation:
        save_path = (args or {}).get("save_path") if isinstance(args, dict) else None
        return write_observation_media(
            obs,
            workspace_scope_id=workspace_scope_id,
            tenant_id=tenant_id,
            save_path=str(save_path) if save_path else None,
        )
    return _writer

"""Unified Preview descriptor and optimistic text-write HTTP surface."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import re
import zipfile
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.auth.deps import AuthContext, current_user, tenant_db
from vibecanvas_api.authorization.dependencies import (
    context_for_auth,
    get_authz_service,
    principal_for_auth,
)
from vibecanvas_api.authorization.service import AuthzService
from vibecanvas_api.authorization.stream_guard import (
    authorization_lease_is_valid,
)
from vibecanvas_api.authorization.types import (
    Action,
    ConsistencyPreference,
    ResourceRef,
    ResourceType,
)
from vibecanvas_api.diagrams.compiler import compile_diagram
from vibecanvas_api.diagrams.isolated_render import (
    render_scene_pdf_isolated,
    render_scene_png_isolated,
    render_scene_svg_isolated,
)
from vibecanvas_api.diagrams.limits import (
    MAX_SOURCE_BYTES,
    DiagramLimitError,
)
from vibecanvas_api.diagrams.validator import parse_and_validate
from vibecanvas_api.schemas.preview import (
    ChatFileRefV1,
    DiagramPreviewExportBody,
    FileRefV1,
    MountFileRefV1,
    PreviewCapabilities,
    PreviewContent,
    PreviewDescriptorV1,
    PreviewErrorInfo,
    PreviewFileWriteOut,
    PreviewFileWriteV1,
    PreviewResolveBody,
    PreviewResourceMount,
    PreviewResourceSession,
    PreviewTextMetadata,
    RunFileRefV1,
)
from vibecanvas_api.services.chat_workspace import chat_workspace_scope_id
from vibecanvas_api.services.file_revision import (
    vfs_content_revision,
    vfs_row_revision,
)
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.services.sandbox.manager import get_sandbox_manager
from vibecanvas_api.services.preview_resource_policy import (
    html_vfs_read_rules,
    rules_for_root,
)
from vibecanvas_api.services.user_mount_workspace import mount_scope_id
from vibecanvas_api.services.vfs_signing import (
    issue_vfs_resource_capability,
    sign_vfs_url,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.diagram_draft_repo import DiagramDraftRepo
from vibecanvas_api.storage.models import (
    DiagramRenderRevision,
    VfsArtifact,
    VfsArtifactEvent,
    VfsRun,
)
from vibecanvas_api.storage.vfs_store import VfsRepo
from vibecanvas_api.streaming.sse import format_event

router = APIRouter(prefix="/api/v1/previews", tags=["previews"])

INLINE_TEXT_BYTES = 1024 * 1024
LARGE_TEXT_SAMPLE_BYTES = 2 * 1024 * 1024
EDITABLE_TEXT_BYTES = 10 * 1024 * 1024
DETECTION_BYTES = 64 * 1024
OFFICE_AUTO_BYTES = 10 * 1024 * 1024
OFFICE_MANUAL_BYTES = 50 * 1024 * 1024
SPREADSHEET_MAX_BYTES = 10 * 1024 * 1024
ARCHIVE_TOTAL_BYTES = 500 * 1024 * 1024
ARCHIVE_ENTRY_BYTES = 100 * 1024 * 1024
ARCHIVE_ENTRIES = 10_000
ARCHIVE_RATIO = 100
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _draft_etag(draft) -> str:
    value = (
        f"{draft.draft_id}:{draft.latest_source_sequence}:"
        f"{draft.latest_ready_sequence}:{draft.status}:{int(draft.terminal)}"
    ).encode()
    return f'"diagram-draft-{hashlib.sha256(value).hexdigest()[:24]}"'


async def _ready_revision_payload(
    *,
    revision: DiagramRenderRevision,
    workspace_scope_id: str,
    session: AsyncSession,
) -> dict:
    if not revision.scene_path or not revision.scene_ref or not revision.scene_hash:
        raise HTTPException(status_code=409, detail="diagram_ready_scene_unavailable")
    raw = await VfsRepo(session, object_store=get_object_store()).read_bytes(
        wf_id=workspace_scope_id,
        path=revision.scene_path,
    )
    if raw is None:
        raise HTTPException(status_code=409, detail="diagram_ready_scene_unavailable")
    actual_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if actual_hash != revision.scene_hash:
        raise HTTPException(status_code=409, detail="diagram_ready_scene_corrupt")
    try:
        scene = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="diagram_ready_scene_corrupt",
        ) from exc
    return {
        "revision_id": str(revision.revision_id),
        "sequence": int(revision.sequence),
        "operation": revision.operation,
        "element_ids": list(revision.element_ids or []),
        "scene_ref": revision.scene_ref,
        "scene_hash": revision.scene_hash,
        "scene": scene,
        "created_at": revision.created_at.isoformat(),
    }


@router.get("/diagram-drafts/{draft_id}/render-revisions")
async def get_diagram_draft_render_revisions(
    draft_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=20),
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
) -> Response:
    """Return only trusted, server-compiled Ready Revisions for one owner.

    The database cursor and Scene bodies live in shared durable storage, so a
    caller may land on any API worker. Draft IDs are lookup keys, never an
    authorization grant.
    """
    repo = DiagramDraftRepo(session)
    draft = await repo.get_owned(draft_id, auth.user_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="diagram_draft_not_found")
    etag = _draft_etag(draft)
    revisions, minimum_ready_sequence = await repo.ready_revisions(
        draft_id,
        after=after,
        limit=limit,
    )
    reset_to_latest = bool(
        minimum_ready_sequence is not None
        and after < minimum_ready_sequence - 1
    )
    if reset_to_latest and int(draft.latest_ready_sequence) > 0:
        revisions, _ = await repo.ready_revisions(
            draft_id,
            after=int(draft.latest_ready_sequence) - 1,
            limit=1,
        )
    if (
        request.headers.get("if-none-match") == etag
        and not revisions
        and not reset_to_latest
    ):
        return Response(status_code=304, headers={"ETag": etag})
    items = [
        await _ready_revision_payload(
            revision=revision,
            workspace_scope_id=draft.workspace_scope_id,
            session=session,
        )
        for revision in revisions
    ]
    payload = {
        "draft_id": str(draft.draft_id),
        "chat_id": draft.chat_id,
        "turn_id": draft.turn_id,
        "status": draft.status,
        "items": items,
        "latest_source_sequence": int(draft.latest_source_sequence),
        "latest_ready_sequence": int(draft.latest_ready_sequence),
        "latest_ready_scene_ref": draft.latest_ready_scene_ref,
        "pending_sequences": list(range(
            int(draft.latest_ready_sequence) + 1,
            min(int(draft.latest_source_sequence), int(draft.latest_ready_sequence) + 20) + 1,
        )),
        "terminal": bool(draft.terminal),
        "reset_to_latest": reset_to_latest,
    }
    return JSONResponse(
        payload,
        headers={
            "ETag": etag,
            "Cache-Control": "private, no-cache",
        },
    )

TEXT_EXTENSIONS = {
    ".css", ".ini", ".js", ".jsx", ".json", ".jsonl", ".log", ".py",
    ".sh", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
    ".csv", ".tsv",
}
LEGACY_OFFICE_EXTENSIONS = {
    ".doc", ".dot", ".odt", ".rtf", ".ppt", ".pps", ".ppsx", ".odp",
    ".xls", ".xlsb", ".xlsm", ".ods",
}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz"}
EDITABLE_CONTENT_TYPES = {
    "application/json", "application/xml", "application/x-yaml",
    "text/html", "text/markdown", "text/plain", "text/x-python", "text/yaml",
}


@dataclass(slots=True)
class _ResolvedFile:
    file_ref: FileRefV1
    row: VfsArtifact | VfsRun
    scope_id: str
    run_id: str


def _file_resource(file_ref: FileRefV1, auth: AuthContext) -> ResourceRef:
    if isinstance(file_ref, ChatFileRefV1):
        return ResourceRef(
            ResourceType.CHAT,
            file_ref.chat_id,
            auth.active_organization_id,
        )
    if isinstance(file_ref, RunFileRefV1):
        return ResourceRef(
            ResourceType.VFS_RUN,
            file_ref.run_id,
            auth.active_organization_id,
        )
    return ResourceRef(
        ResourceType.STORAGE_ROOT,
        auth.user_id,
        auth.active_organization_id,
    )


async def _authorize_file_ref(
    *,
    file_ref: FileRefV1,
    request: Request,
    auth: AuthContext,
    service: AuthzService,
    action: Action,
    consistency: ConsistencyPreference = ConsistencyPreference.MINIMIZE_LATENCY,
) -> None:
    decision = await service.check(
        principal_for_auth(auth),
        action,
        _file_resource(file_ref, auth),
        context_for_auth(auth, request, consistency=consistency),
    )
    if not decision.allowed:
        raise HTTPException(status_code=404, detail="preview_file_not_found")


async def _resolve_file(
    *,
    file_ref: FileRefV1,
    auth: AuthContext,
    session: AsyncSession,
    lock: bool = False,
) -> _ResolvedFile:
    if isinstance(file_ref, ChatFileRefV1):
        scope_id = chat_workspace_scope_id(file_ref.chat_id)
        query = select(VfsArtifact).where(
            VfsArtifact.scope_id == scope_id,
            VfsArtifact.path == file_ref.path,
        )
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query)).scalar_one_or_none()
        run_id = ""
    elif isinstance(file_ref, MountFileRefV1):
        scope_id = mount_scope_id(auth.user_id)
        query = select(VfsArtifact).where(
            VfsArtifact.scope_id == scope_id,
            VfsArtifact.path == file_ref.path,
        )
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query)).scalar_one_or_none()
        run_id = ""
    elif isinstance(file_ref, RunFileRefV1):
        scope_id = ""
        query = select(VfsRun).where(
            VfsRun.run_id == file_ref.run_id,
            VfsRun.path == file_ref.path,
        )
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query)).scalar_one_or_none()
        run_id = file_ref.run_id
    else:  # pragma: no cover - Pydantic discriminator is exhaustive
        raise HTTPException(status_code=400, detail="invalid_file_ref")
    if row is None or not row.object_key:
        raise HTTPException(status_code=404, detail="preview_file_not_found")
    return _ResolvedFile(file_ref=file_ref, row=row, scope_id=scope_id, run_id=run_id)


def _safe_inline_url(*, resolved: _ResolvedFile, auth: AuthContext) -> str:
    return sign_vfs_url(
        tenant_id=auth.tenant_id,
        path=resolved.file_ref.path,
        wf_id=resolved.scope_id,
        run_id=resolved.run_id,
        expires_in_s=300,
    )


def _preview_error(code: str, **params) -> PreviewErrorInfo:
    return PreviewErrorInfo(code=code, params=params)


def _zip_error(data: bytes) -> PreviewErrorInfo | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > ARCHIVE_ENTRIES:
                return _preview_error(
                    "too_many_archive_entries",
                    actual=len(infos),
                    limit=ARCHIVE_ENTRIES,
                )
            total = 0
            compressed = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    return _preview_error("encrypted_file")
                if info.file_size > ARCHIVE_ENTRY_BYTES:
                    return _preview_error(
                        "archive_entry_too_large",
                        actualBytes=info.file_size,
                        limitBytes=ARCHIVE_ENTRY_BYTES,
                    )
                total += info.file_size
                compressed += max(1, info.compress_size)
                if total > ARCHIVE_TOTAL_BYTES:
                    return _preview_error(
                        "archive_expanded_too_large",
                        actualBytes=total,
                        limitBytes=ARCHIVE_TOTAL_BYTES,
                    )
            if total / max(1, compressed) > ARCHIVE_RATIO:
                return _preview_error(
                    "archive_compression_ratio_too_high",
                    limit=ARCHIVE_RATIO,
                )
    except (zipfile.BadZipFile, OSError):
        return _preview_error("invalid_file")
    return None


def _text_metadata(data: bytes) -> tuple[str | None, PreviewTextMetadata | None, str | None]:
    bom = data.startswith(b"\xef\xbb\xbf")
    body = data[3:] if bom else data
    try:
        value = body.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, "unsupported_text_encoding"
    crlf = value.count("\r\n")
    lf = value.count("\n") - crlf
    newline = "CRLF" if crlf > lf else "LF"
    mixed = crlf > 0 and lf > 0
    return value, PreviewTextMetadata(bom=bom, newline=newline, mixedNewlines=mixed), None


def _detect(path: str, content_type: str, data: bytes) -> tuple[str, str]:
    ext = os.path.splitext(path.lower())[1]
    mime = (content_type or "").split(";", 1)[0].lower()
    if path.lower().endswith(".vdiagram.json"):
        return "diagram", "application/vnd.vibecanvas.diagram+json"
    if data.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image", "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image", "image/webp"
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf", "application/pdf"
    if ext in {".csv"} or mime in {"table/csv", "text/csv"}:
        return "csv", "text/csv"
    if ext in {".tsv"} or mime in {"table/tsv", "text/tab-separated-values"}:
        return "tsv", "text/tab-separated-values"
    if ext in {".jsonl", ".ndjson"} or mime in {"table/jsonl", "application/x-ndjson"}:
        return "jsonl", "application/x-ndjson"
    if ext == ".md" or mime in {"text/markdown", "text/x-markdown"}:
        return "markdown", "text/markdown"
    if ext in {".html", ".htm"} or mime in {"text/html", "application/xhtml+xml"}:
        return "html", "text/html"
    if ext == ".docx" or "wordprocessingml.document" in mime:
        return "docx", mime or "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext == ".pptx" or "presentationml.presentation" in mime:
        return "pptx", mime or "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if ext == ".xlsx" or "spreadsheetml.sheet" in mime:
        return "spreadsheet", mime or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext in LEGACY_OFFICE_EXTENSIONS:
        return "legacy_office", mime or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive", mime or "application/octet-stream"
    if mime.startswith("image/"):
        return "image", mime
    if mime.startswith("audio/"):
        return "audio", mime
    if mime.startswith("video/"):
        return "video", mime
    if (
        ext in TEXT_EXTENSIONS
        or mime.startswith("text/")
        or mime in {"application/json", "application/xml", "application/x-yaml"}
        or mime.startswith("table/")
    ):
        return "text", mime or mimetypes.guess_type(path)[0] or "text/plain"
    return "unsupported", mime or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _descriptor(
    *,
    resolved: _ResolvedFile,
    auth: AuthContext,
    data: bytes,
) -> PreviewDescriptorV1:
    row = resolved.row
    path = resolved.file_ref.path
    detected, content_type = _detect(path, row.content_type, data)
    size = int(row.size_bytes)
    error = None
    text_meta = None
    content = None
    renderer = detected
    load_policy = "unsupported"
    editable = False

    diagram = None
    if detected == "diagram":
        document, issues = parse_and_validate(data)
        renderer = "diagram"
        load_policy = "inline"
        if document is not None and not any(issue.severity == "error" for issue in issues):
            try:
                scene = compile_diagram(document)
            except DiagramLimitError as exc:
                diagram = {
                    "status": "invalid",
                    "scene": None,
                    "issues": [
                        *[
                            issue.model_dump(mode="json", by_alias=True)
                            for issue in issues
                        ],
                        {
                            "severity": "error",
                            "stage": "compile",
                            "code": exc.code,
                            "json_pointer": "/view",
                            "message": str(exc),
                        },
                    ],
                }
            else:
                all_issues = [*issues, *scene.issues]
                diagram = {
                    "status": "valid",
                    "scene": scene.model_dump(mode="json", by_alias=True),
                    "issues": [
                        issue.model_dump(mode="json", by_alias=True)
                        for issue in all_issues
                    ],
                    "sourceHash": f"sha256:{hashlib.sha256(data).hexdigest()}",
                }
        else:
            diagram = {
                "status": "invalid",
                "scene": None,
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in issues],
            }
        content = PreviewContent(
            url=_safe_inline_url(resolved=resolved, auth=auth),
            rangeSupported=True,
        )
    elif (
        detected in {"csv", "tsv", "jsonl"}
        and size > SPREADSHEET_MAX_BYTES
    ):
        renderer = "unsupported"
        error = _preview_error(
            "file_too_large",
            actualBytes=size,
            limitBytes=SPREADSHEET_MAX_BYTES,
        )
    elif detected in {"text", "markdown", "html", "csv", "tsv", "jsonl"}:
        text_value, text_meta, text_error = _text_metadata(data)
        renderer = (
            detected
            if detected in {"markdown", "html"}
            else "spreadsheet"
            if detected in {"csv", "tsv", "jsonl"}
            else "text"
        )
        if text_value is not None:
            editable = (
                detected in {"text", "markdown", "html"}
                and not isinstance(resolved.file_ref, RunFileRefV1)
                and (
                    not isinstance(resolved.file_ref, ChatFileRefV1)
                    or resolved.file_ref.path.startswith("/data/")
                )
                and size <= EDITABLE_TEXT_BYTES
            )
            if size <= INLINE_TEXT_BYTES:
                load_policy = "inline"
                content = PreviewContent(inlineText=text_value)
            else:
                load_policy = "stream"
                content = PreviewContent(
                    url=_safe_inline_url(resolved=resolved, auth=auth),
                    truncated=True,
                    rangeSupported=True,
                )
        else:
            renderer = "unsupported"
            error = _preview_error(text_error or "invalid_file")
    elif detected in {"pdf", "image", "audio", "video"}:
        load_policy = "range"
        content = PreviewContent(
            url=_safe_inline_url(resolved=resolved, auth=auth),
            rangeSupported=True,
        )
    elif detected in {"docx", "pptx", "spreadsheet"}:
        size_limit = (
            SPREADSHEET_MAX_BYTES
            if detected == "spreadsheet"
            else OFFICE_MANUAL_BYTES
        )
        if size > size_limit:
            renderer = "unsupported"
            load_policy = "unsupported"
            error = _preview_error(
                "file_too_large",
                actualBytes=size,
                limitBytes=size_limit,
            )
        else:
            error = _zip_error(data)
            if error:
                renderer = "unsupported"
            else:
                load_policy = (
                    "inline"
                    if detected == "spreadsheet" or size <= OFFICE_AUTO_BYTES
                    else "manual"
                )
                content = PreviewContent(
                    url=_safe_inline_url(resolved=resolved, auth=auth),
                    rangeSupported=True,
                )
    elif detected == "legacy_office":
        renderer = "unsupported"
        error = _preview_error(
            "unsupported_file_type",
            extension=os.path.splitext(path.lower())[1],
        )
    else:
        renderer = "unsupported"
        error = _preview_error(
            "archive_preview_not_supported"
            if detected == "archive"
            else "unsupported_file_type"
        )

    # Download remains available for unsupported files. This URL is still
    # scoped, short-lived and object-key-free; renderers may ignore it.
    if content is None:
        content = PreviewContent(
            url=_safe_inline_url(resolved=resolved, auth=auth),
            rangeSupported=True,
        )
    preview = renderer != "unsupported"
    return PreviewDescriptorV1(
        fileRef=resolved.file_ref,
        name=path.rsplit("/", 1)[-1],
        sizeBytes=size,
        contentType=content_type,
        detectedType=detected,
        revision=vfs_row_revision(row),
        renderer=renderer,
        loadPolicy=load_policy,
        capabilities=PreviewCapabilities(
            preview=preview,
            edit=editable,
            download=True,
        ),
        content=content,
        text=text_meta,
        diagram=diagram,
        error=error,
    )


def _object_prefix(object_key: str, size_bytes: int, limit: int) -> bytes:
    if size_bytes <= 0 or limit <= 0:
        return b""
    end = min(size_bytes, limit) - 1
    return b"".join(
        get_object_store().iter_bytes(object_key, start=0, end=end)
    )


def _event_file_ref(
    *,
    scope: Literal["chat", "mount", "run"],
    path: str,
    chat_id: str | None,
    run_id: str | None,
) -> FileRefV1:
    """Build the validated FileRef represented by an SSE query string."""
    try:
        if scope == "chat":
            return ChatFileRefV1(
                schema_version=1,
                scope="chat",
                chat_id=chat_id or "",
                path=path,
            )
        if scope == "mount":
            return MountFileRefV1(
                schema_version=1,
                scope="mount",
                path=path,
            )
        return RunFileRefV1(
            schema_version=1,
            scope="run",
            run_id=run_id or "",
            path=path,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="invalid_preview_event_subscription",
        ) from exc


def _event_scope(
    *, file_ref: FileRefV1, user_id: str
) -> tuple[str, str]:
    if isinstance(file_ref, ChatFileRefV1):
        return "artifact", chat_workspace_scope_id(file_ref.chat_id)
    if isinstance(file_ref, MountFileRefV1):
        return "artifact", mount_scope_id(user_id)
    return "run", file_ref.run_id


def _event_paths(path: str) -> tuple[str, ...]:
    return (path,)


@router.get("/events")
async def stream_preview_file_events(
    request: Request,
    scope: Literal["chat", "mount", "run"] = Query(),
    path: str = Query(min_length=1),
    chat_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
) -> StreamingResponse:
    """Reconcile once, then follow durable changes for one Preview FileRef.

    The browser never polls descriptors. Database triggers append a cursor in
    the same transaction as every VFS content mutation, so any API worker can
    replay changes after reconnect or another worker's write. The initial
    ``preview_ready`` frame carries the authoritative revision and closes the
    resolve/subscribe race without replaying unbounded historical changes.
    """

    file_ref = _event_file_ref(
        scope=scope,
        path=path,
        chat_id=chat_id,
        run_id=run_id,
    )
    await _authorize_file_ref(
        file_ref=file_ref,
        request=request,
        auth=auth,
        service=service,
        action=Action.VIEW,
    )
    scope_kind, storage_scope_id = _event_scope(
        file_ref=file_ref,
        user_id=auth.user_id,
    )
    watched_paths = _event_paths(path)
    raw_cursor = request.headers.get("last-event-id", "")
    try:
        cursor = max(0, int(raw_cursor or 0))
    except ValueError:
        cursor = 0

    async with session_scope(tenant_id=auth.tenant_id) as initial_session:
        # For a fresh subscription, establish the tail cursor before resolving
        # the current row. A write in either side of these statements is then
        # caught by the ready revision or by replay after the cursor.
        latest_event = (
            await initial_session.execute(
                select(VfsArtifactEvent)
                .where(
                    VfsArtifactEvent.scope_kind == scope_kind,
                    VfsArtifactEvent.scope_id == storage_scope_id,
                    VfsArtifactEvent.path.in_(watched_paths),
                )
                .order_by(VfsArtifactEvent.event_id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cursor == 0:
            cursor = int(latest_event.event_id) if latest_event is not None else 0
        resolved = await _resolve_file(
            file_ref=file_ref,
            auth=auth,
            session=initial_session,
        )
        current_revision = vfs_row_revision(resolved.row)
        current_commit_event = (
            latest_event
            if latest_event is not None
            and latest_event.event_type == "upsert"
            and latest_event.content_revision
            and vfs_content_revision(latest_event.content_revision) == current_revision
            else None
        )

    async def event_stream():
        nonlocal cursor
        if isinstance(file_ref, ChatFileRefV1):
            stream_resource = ResourceRef(
                ResourceType.CHAT,
                file_ref.chat_id,
                auth.active_organization_id,
            )
        elif isinstance(file_ref, RunFileRefV1):
            stream_resource = ResourceRef(
                ResourceType.VFS_RUN,
                file_ref.run_id,
                auth.active_organization_id,
            )
        else:
            stream_resource = ResourceRef(
                ResourceType.STORAGE_ROOT,
                auth.user_id,
                auth.active_organization_id,
            )
        next_authorization_check = 0.0

        async def authorized() -> bool:
            nonlocal next_authorization_check
            now = asyncio.get_running_loop().time()
            if now < next_authorization_check:
                return True
            allowed = await authorization_lease_is_valid(
                auth=auth,
                openfga_client=getattr(
                    request.app.state, "openfga_client", None
                ),
                resource=stream_resource,
                action=Action.VIEW,
            )
            next_authorization_check = now + 5.0
            return allowed

        if not await authorized():
            return
        yield format_event(
            "preview_ready",
            {
                "event_id": cursor,
                "path": path,
                "revision": current_revision,
                "committed_at": (
                    current_commit_event.created_at.isoformat()
                    if current_commit_event is not None
                    else None
                ),
                "committed_event_id": (
                    int(current_commit_event.event_id)
                    if current_commit_event is not None
                    else None
                ),
            },
            event_id=cursor,
        )
        idle_ticks = 0
        while not await request.is_disconnected():
            if not await authorized():
                return
            async with session_scope(tenant_id=auth.tenant_id) as event_session:
                events = (
                    await event_session.execute(
                        select(VfsArtifactEvent)
                        .where(
                            VfsArtifactEvent.scope_kind == scope_kind,
                            VfsArtifactEvent.scope_id == storage_scope_id,
                            VfsArtifactEvent.path.in_(watched_paths),
                            VfsArtifactEvent.event_id > cursor,
                        )
                        .order_by(VfsArtifactEvent.event_id)
                        .limit(100)
                    )
                ).scalars().all()
            if events:
                idle_ticks = 0
                for event in events:
                    cursor = int(event.event_id)
                    is_source = event.path == path
                    revision = (
                        vfs_content_revision(event.content_revision)
                        if is_source
                        and event.event_type == "upsert"
                        and event.content_revision
                        else None
                    )
                    yield format_event(
                        "preview_file",
                        {
                            "event_id": cursor,
                            "path": path,
                            "changed_path": event.path,
                            "event_type": event.event_type,
                            "revision": revision,
                            "derived": not is_source,
                            "created_at": event.created_at.isoformat(),
                        },
                        event_id=cursor,
                    )
                continue
            idle_ticks += 1
            if idle_ticks >= 15:
                idle_ticks = 0
                yield b": heartbeat\n\n"
            # Only the backend observes its durable event cursor. The browser
            # receives frames on change and never refetches on a timer.
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/resolve", response_model=PreviewDescriptorV1)
async def resolve_preview(
    body: PreviewResolveBody,
    request: Request,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> PreviewDescriptorV1:
    await _authorize_file_ref(
        file_ref=body.file_ref,
        request=request,
        auth=auth,
        service=service,
        action=Action.VIEW,
    )
    resolved = await _resolve_file(
        file_ref=body.file_ref,
        auth=auth,
        session=session,
    )
    size = int(resolved.row.size_bytes)
    prefix = _object_prefix(resolved.row.object_key, size, DETECTION_BYTES)
    detected, _content_type = _detect(
        resolved.file_ref.path,
        resolved.row.content_type,
        prefix,
    )
    if detected == "diagram":
        data = _object_prefix(resolved.row.object_key, size, MAX_SOURCE_BYTES + 1)
    elif detected in {"text", "markdown", "html", "csv", "tsv", "jsonl"}:
        # Full strict UTF-8 validation is required for bounded text and
        # structured-table previews. Only source-like text renderers are
        # editable; CSV/TSV/JSONL stay read-only to avoid maintaining a second
        # structured write/serialization path.
        limit = size if size <= EDITABLE_TEXT_BYTES else LARGE_TEXT_SAMPLE_BYTES
        data = _object_prefix(resolved.row.object_key, size, limit)
    elif (
        detected in {"docx", "pptx"}
        and size <= OFFICE_MANUAL_BYTES
    ) or (
        detected == "spreadsheet"
        and size <= SPREADSHEET_MAX_BYTES
    ):
        # OOXML preflight must inspect the complete bounded ZIP.
        data = get_object_store().fetch_bytes(resolved.row.object_key)
    else:
        data = prefix
    descriptor = _descriptor(resolved=resolved, auth=auth, data=data)
    return descriptor


@router.post("/diagram/export")
async def export_preview_diagram(
    body: DiagramPreviewExportBody,
    request: Request,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> Response:
    """Export the exact diagram revision currently open in Preview."""
    await _authorize_file_ref(
        file_ref=body.file_ref,
        request=request,
        auth=auth,
        service=service,
        action=Action.VIEW,
    )
    resolved = await _resolve_file(
        file_ref=body.file_ref,
        auth=auth,
        session=session,
    )
    if not body.file_ref.path.lower().endswith(".vdiagram.json"):
        raise HTTPException(status_code=415, detail="preview_not_a_diagram")
    if vfs_row_revision(resolved.row) != body.expected_revision:
        raise HTTPException(status_code=409, detail="preview_revision_conflict")
    if int(resolved.row.size_bytes) > MAX_SOURCE_BYTES:
        raise HTTPException(status_code=413, detail="diagram_source_too_large")
    raw = get_object_store().fetch_bytes(resolved.row.object_key)
    document, issues = parse_and_validate(raw)
    if document is None or any(issue.severity == "error" for issue in issues):
        raise HTTPException(status_code=422, detail="diagram_not_compile_ready")
    try:
        scene = compile_diagram(document)
        if body.format == "svg":
            payload = await render_scene_svg_isolated(
                scene,
                theme=body.theme,
                background=body.background,
            )
            media_type = "image/svg+xml"
        elif body.format == "png":
            payload = await render_scene_png_isolated(
                scene,
                theme=body.theme,
                max_width=min(2400, round(1600 * body.scale)),
                max_height=min(1600, round(1000 * body.scale)),
                background=body.background,
            )
            media_type = "image/png"
        else:
            payload = await render_scene_pdf_isolated(
                scene,
                theme=body.theme,
                background=body.background,
            )
            media_type = "application/pdf"
    except DiagramLimitError as exc:
        raise HTTPException(
            status_code=504 if exc.code.endswith("_timeout") else 413,
            detail=exc.code,
        ) from exc
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", document.id).strip("-.") or "diagram"
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{body.format}"',
            "ETag": f'"{hashlib.sha256(payload).hexdigest()}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/resource-session", response_model=PreviewResourceSession)
async def create_preview_resource_session(
    body: PreviewResolveBody,
    request: Request,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> PreviewResourceSession:
    """Mint opaque resource mounts for an HTML file Preview.

    FileRef ownership is resolved before capabilities are issued. The iframe
    only receives virtual path mounts and never learns workspace/object-store
    identifiers.
    """
    await _authorize_file_ref(
        file_ref=body.file_ref,
        request=request,
        auth=auth,
        service=service,
        action=Action.VIEW,
    )
    resolved = await _resolve_file(
        file_ref=body.file_ref,
        auth=auth,
        session=session,
    )
    ttl = 3600
    mounts: list[PreviewResourceMount] = []
    source_path = body.file_ref.path
    rules: set[str] = {source_path}
    content_type = str(resolved.row.content_type or "").lower()
    if content_type in {"text/html", "application/xhtml+xml"} or source_path.lower().endswith(
        (".html", ".htm")
    ):
        size_bytes = int(resolved.row.size_bytes or 0)
        sample_size = min(size_bytes, 2 * 1024 * 1024)
        html = _object_prefix(resolved.row.object_key, size_bytes, sample_size)
        rules.update(html_vfs_read_rules(html.decode("utf-8", "replace")))
    sorted_rules = tuple(sorted(rules))
    if isinstance(body.file_ref, ChatFileRefV1):
        workspace_rules = tuple(
            rule
            for root in ("data", "memory", "logs")
            for rule in rules_for_root(sorted_rules, root)
        )
        workspace_capability = issue_vfs_resource_capability(
            tenant_id=auth.tenant_id,
            audience="file-preview",
            allowed_paths=workspace_rules,
            wf_id=resolved.scope_id,
            expires_in_s=ttl,
        )
        workspace_root = (
            f"/api/v1/vfs/resources/file-preview/"
            f"{quote(workspace_capability, safe='-_')}/"
        )
        mounts = [PreviewResourceMount(pathPrefix="/", rootUrl=workspace_root)]
        mount_rules = rules_for_root(sorted_rules, "mount")
        if mount_rules:
            mount_capability = issue_vfs_resource_capability(
                tenant_id=auth.tenant_id,
                audience="file-preview",
                allowed_paths=mount_rules,
                wf_id=mount_scope_id(auth.user_id),
                expires_in_s=ttl,
            )
            mounts.insert(0, PreviewResourceMount(
                pathPrefix="/mount/",
                rootUrl=(
                    f"/api/v1/vfs/resources/file-preview/"
                    f"{quote(mount_capability, safe='-_')}/mount/"
                ),
            ))
        source_dir = source_path.rsplit("/", 1)[0].lstrip("/")
        base_url = workspace_root + (source_dir + "/" if source_dir else "")
    elif isinstance(body.file_ref, MountFileRefV1):
        mount_rules = rules_for_root(sorted_rules, "mount")
        capability = issue_vfs_resource_capability(
            tenant_id=auth.tenant_id,
            audience="file-preview",
            allowed_paths=mount_rules,
            wf_id=resolved.scope_id,
            expires_in_s=ttl,
        )
        root = (
            f"/api/v1/vfs/resources/file-preview/"
            f"{quote(capability, safe='-_')}/mount/"
        )
        mounts = [PreviewResourceMount(pathPrefix="/mount/", rootUrl=root)]
        source_dir = source_path[len("/mount/"):].rsplit("/", 1)[0]
        base_url = root + (source_dir + "/" if source_dir else "")
    else:
        run_rules = rules_for_root(sorted_rules, "run")
        capability = issue_vfs_resource_capability(
            tenant_id=auth.tenant_id,
            audience="file-preview",
            allowed_paths=run_rules,
            run_id=resolved.run_id,
            expires_in_s=ttl,
        )
        root = (
            f"/api/v1/vfs/resources/file-preview/"
            f"{quote(capability, safe='-_')}/run/"
        )
        mounts = [PreviewResourceMount(pathPrefix="/run/", rootUrl=root)]
        source_dir = source_path[len("/run/"):].rsplit("/", 1)[0]
        base_url = root + (source_dir + "/" if source_dir else "")
    return PreviewResourceSession(
        resourceMounts=mounts,
        baseUrl=base_url,
        expiresIn=ttl,
    )


@router.put("/file", response_model=PreviewFileWriteOut)
async def write_preview_file(
    body: PreviewFileWriteV1,
    request: Request,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> PreviewFileWriteOut:
    if (
        isinstance(body.file_ref, RunFileRefV1)
        or (
            isinstance(body.file_ref, ChatFileRefV1)
            and not body.file_ref.path.startswith("/data/")
        )
    ):
        raise HTTPException(status_code=403, detail="preview_file_read_only")
    await _authorize_file_ref(
        file_ref=body.file_ref,
        request=request,
        auth=auth,
        service=service,
        action=Action.UPDATE,
        consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
    )
    lock_key = body.file_ref.model_dump_json(by_alias=True)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"preview-write:{lock_key}"},
    )
    resolved = await _resolve_file(
        file_ref=body.file_ref,
        auth=auth,
        session=session,
        lock=True,
    )
    if vfs_row_revision(resolved.row) != body.expected_revision:
        raise HTTPException(status_code=409, detail="preview_revision_conflict")
    original = get_object_store().fetch_bytes(resolved.row.object_key)
    detected, _detected_content_type = _detect(
        body.file_ref.path,
        resolved.row.content_type,
        original,
    )
    requested_content_type = body.content_type.split(";", 1)[0].strip().lower()
    if (
        detected not in {"text", "markdown", "html"}
        or (
            not requested_content_type.startswith("text/")
            and requested_content_type not in EDITABLE_CONTENT_TYPES
        )
    ):
        raise HTTPException(status_code=403, detail="preview_file_read_only")
    _original_text, metadata, warning = _text_metadata(original)
    if metadata is None or warning:
        raise HTTPException(status_code=415, detail="preview_text_encoding_not_editable")
    normalized = body.content.replace("\r\n", "\n").replace("\r", "\n")
    if metadata.newline == "CRLF":
        normalized = normalized.replace("\n", "\r\n")
    data = normalized.encode("utf-8")
    if metadata.bom:
        data = b"\xef\xbb\xbf" + data
    repo = VfsRepo(session, object_store=get_object_store())
    await repo.upsert_artifact_bytes(
        wf_id=resolved.scope_id,
        tenant=auth.tenant_id,
        path=body.file_ref.path,
        data=data,
        content_type=requested_content_type,
    )
    # Re-read the ORM row after ON CONFLICT UPDATE so the returned revision is
    # derived from the committed metadata rather than client input.
    await session.refresh(resolved.row)
    await session.commit()
    # Preview is another VFS writer. Keep an already-mounted Agent workspace in
    # the same state immediately; a cold sandbox will hydrate from durable VFS.
    await get_sandbox_manager().mirror_vfs_write(
        auth.tenant_id,
        resolved.scope_id,
        body.file_ref.path,
        data,
    )
    return PreviewFileWriteOut(
        fileRef=body.file_ref,
        revision=vfs_row_revision(resolved.row),
        sizeBytes=len(data),
        contentType=requested_content_type,
    )

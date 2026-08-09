from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.diagrams.compiler import compile_diagram
from vibecanvas_api.diagrams.registry import get_diagram_type
from vibecanvas_api.diagrams.validator import parse_and_validate
from vibecanvas_api.services.chat_workspace import chat_workspace_scope_id
from vibecanvas_api.services.file_revision import vfs_content_revision
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.diagram_draft_repo import DiagramDraftRepo
from vibecanvas_api.storage.vfs_store import VfsRepo


async def _register(client) -> tuple[dict, dict]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"preview_{uuid.uuid4().hex[:12]}@example.com",
            "username": "Preview User",
            "password": "pw12345678",
        },
    )
    assert response.status_code in (200, 201), response.text
    headers = {"Authorization": f"Bearer {response.json()['session_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return headers, me


async def _chat_fixture(client, app_engine) -> tuple[dict, dict, str, str]:
    headers, me = await _register(client)
    boot = await client.get("/api/v1/chats/bootstrap?surface=chat", headers=headers)
    chat_id = f"c_preview_{uuid.uuid4().hex[:10]}"
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        await ChatRepo(session, me["user_id"]).register_session(
            boot.json()["carrier_scope_id"],
            name="Preview",
            chat_id=chat_id,
            surface="chat",
        )
    return headers, me, chat_id, chat_workspace_scope_id(chat_id)


async def _upload(client, headers, scope_id: str, name: str, data: bytes, mime: str):
    response = await client.post(
        f"/api/v1/vfs/upload?wf_id={scope_id}&folder=data",
        headers=headers,
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 200, response.text
    return response.json()["path"]


async def _write_chat_workspace_file(
    *, tenant_id: str, scope_id: str, path: str, data: bytes, mime: str
) -> None:
    async with session_scope(tenant_id=tenant_id) as session:
        await VfsRepo(session, object_store=get_object_store()).upsert_artifact_bytes(
            wf_id=scope_id,
            tenant=tenant_id,
            path=path,
            data=data,
            content_type=mime,
        )


async def _artifact_events(
    app_engine,
    *,
    tenant_id: str,
    scope_id: str,
    path: str,
) -> list[dict]:
    async with app_engine.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id',:tenant,true)"),
            {"tenant": tenant_id},
        )
        rows = (
            await connection.execute(
                text(
                    "SELECT event_id, event_type, content_revision "
                    "FROM vfs_artifact_events "
                    "WHERE scope_kind = 'artifact' "
                    "AND scope_id = :scope_id AND path = :path "
                    "ORDER BY event_id"
                ),
                {"scope_id": scope_id, "path": path},
            )
        ).mappings().all()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_diagram_draft_ready_cursor_etag_and_tenant_isolation(
    client,
    app_engine,
):
    headers, me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    spec = get_diagram_type("flow", "basic")
    assert spec is not None
    source = {
        "schemaVersion": 1,
        "id": "live-flow",
        "title": "Live flow",
        "diagram": {"family": "flow", "type": "basic"},
        "model": {
            "nodes": [
                {"id": "start", "kind": "start", "label": "Start"},
                {"id": "done", "kind": "end", "label": "Done"},
            ],
            "edges": [
                {"id": "complete", "source": "start", "target": "done"},
            ],
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
            "specVersion": "2026.08.1",
            "specHash": spec.spec_hash,
            "compilerVersion": None,
            "themeVersion": None,
        },
    }
    raw = json.dumps(source, sort_keys=True).encode()
    source_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    document, issues = parse_and_validate(raw)
    assert document is not None and issues == []
    scene_bytes = compile_diagram(document).model_dump_json(
        by_alias=True,
        exclude_none=True,
    ).encode()
    scene_hash = f"sha256:{hashlib.sha256(scene_bytes).hexdigest()}"
    turn_id = f"turn-{uuid.uuid4().hex}"
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        repo = DiagramDraftRepo(session)
        cursor = await repo.begin_source(
            tenant_id=me["tenant_id"],
            owner_user_id=me["user_id"],
            chat_id=chat_id,
            turn_id=turn_id,
            workspace_scope_id=scope_id,
            source_path="/memory/diagram-drafts/live.vdiagram.json",
            target_path="/data/diagrams/live.vdiagram.json",
            source_hash=source_hash,
        )
        await repo.mark_compiling(cursor.draft_id, cursor.sequence)
        await repo.mark_ready(
            draft_id=cursor.draft_id,
            sequence=cursor.sequence,
            tenant_id=me["tenant_id"],
            workspace_scope_id=scope_id,
            source_hash=source_hash,
            scene_ref=f"scene://{scene_hash}",
            scene_hash=scene_hash,
            scene_bytes=scene_bytes,
            operation="create_diagram",
            element_ids=["start", "done", "complete"],
        )

    url = (
        f"/api/v1/previews/diagram-drafts/{cursor.draft_id}/"
        "render-revisions?after=0&limit=20"
    )
    first = await client.get(url, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["latest_source_sequence"] == 1
    assert body["latest_ready_sequence"] == 1
    assert body["items"][0]["sequence"] == 1
    assert body["items"][0]["scene_hash"] == scene_hash
    assert body["items"][0]["scene"]["diagramId"] == "live-flow"
    etag = first.headers["etag"]

    unchanged = await client.get(
        url.replace("after=0", "after=1"),
        headers={**headers, "If-None-Match": etag},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""

    invalid_raw = b'{"schemaVersion":1'
    invalid_hash = f"sha256:{hashlib.sha256(invalid_raw).hexdigest()}"
    async with session_scope(tenant_id=me["tenant_id"]) as session:
        repo = DiagramDraftRepo(session)
        invalid_cursor = await repo.begin_source(
            tenant_id=me["tenant_id"],
            owner_user_id=me["user_id"],
            chat_id=chat_id,
            turn_id=turn_id,
            workspace_scope_id=scope_id,
            source_path="/memory/diagram-drafts/live.vdiagram.json",
            target_path="/data/diagrams/live.vdiagram.json",
            source_hash=invalid_hash,
        )
        await repo.mark_invalid(invalid_cursor.draft_id, invalid_cursor.sequence)

    invalid = await client.get(
        url.replace("after=0", "after=1"),
        headers={**headers, "If-None-Match": etag},
    )
    assert invalid.status_code == 200, invalid.text
    assert invalid.json()["status"] == "invalid"
    assert invalid.json()["items"] == []
    assert invalid.json()["latest_ready_sequence"] == 1

    async with session_scope(tenant_id=me["tenant_id"]) as session:
        await DiagramDraftRepo(session).finalize_latest(
            chat_id=chat_id,
            turn_id=turn_id,
            completed=False,
        )
    terminal = await client.get(url.replace("after=0", "after=1"), headers=headers)
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["status"] == "cancelled"
    assert terminal.json()["terminal"] is True
    assert terminal.json()["latest_ready_sequence"] == 1
    assert terminal.json()["items"] == []

    other_headers, _other = await _register(client)
    denied = await client.get(url, headers=other_headers)
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_vdiagram_resolves_to_compiled_read_only_scene(client, app_engine):
    headers, _me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    spec = get_diagram_type("flow", "basic")
    assert spec is not None
    source = {
        "schemaVersion": 1,
        "id": "preview-flow",
        "title": "Preview flow",
        "diagram": {"family": "flow", "type": "basic"},
        "model": {
            "nodes": [
                {"id": "start", "kind": "start", "label": "Start", "styleRole": "primary"},
                {"id": "done", "kind": "end", "label": "Done", "styleRole": "success"},
            ],
            "edges": [{"id": "complete", "source": "start", "target": "done", "kind": "flow"}],
            "groups": [], "embeds": [], "resources": [],
        },
        "intent": {"direction": "RIGHT", "density": "comfortable", "stability": "preserve", "primaryPath": ["start", "done"], "constraints": []},
        "view": {"layoutMode": "auto", "overrides": {}, "frames": []},
        "metadata": {"createdBy": "agent", "specVersion": "2026.08.1", "specHash": spec.spec_hash, "compilerVersion": None, "themeVersion": None},
    }
    path = await _upload(
        client,
        headers,
        scope_id,
        "preview-flow.vdiagram.json",
        json.dumps(source).encode(),
        "application/json",
    )
    resolved = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": {"schemaVersion": 1, "scope": "chat", "chatId": chat_id, "path": path}},
    )
    assert resolved.status_code == 200, resolved.text
    descriptor = resolved.json()
    assert descriptor["renderer"] == "diagram"
    assert descriptor["detectedType"] == "diagram"
    assert descriptor["capabilities"] == {"preview": True, "edit": False, "download": True}
    assert descriptor["diagram"]["status"] == "valid"
    assert [node["id"] for node in descriptor["diagram"]["scene"]["nodes"]] == ["start", "done"]

    exported = await client.post(
        "/api/v1/previews/diagram/export",
        headers=headers,
        json={
            "fileRef": {"schemaVersion": 1, "scope": "chat", "chatId": chat_id, "path": path},
            "expectedRevision": descriptor["revision"],
            "format": "svg",
            "theme": "light",
            "background": "white",
        },
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("image/svg+xml")
    assert exported.content.startswith(b"<svg")
    assert b'fill="#ffffff"' in exported.content

    dark_export = await client.post(
        "/api/v1/previews/diagram/export",
        headers=headers,
        json={
            "fileRef": {
                "schemaVersion": 1,
                "scope": "chat",
                "chatId": chat_id,
                "path": path,
            },
            "expectedRevision": descriptor["revision"],
            "format": "svg",
            "theme": "dark",
            "background": "theme",
        },
    )
    assert dark_export.status_code == 422


@pytest.mark.asyncio
async def test_preview_resolve_and_text_write_preserve_bom_newlines_and_revision(
    client, app_engine,
):
    headers, me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    path = await _upload(
        client,
        headers,
        scope_id,
        "notes.md",
        b"\xef\xbb\xbf# Title\r\n\r\nBefore\r\n",
        "text/markdown",
    )
    file_ref = {
        "schemaVersion": 1,
        "scope": "chat",
        "chatId": chat_id,
        "path": path,
    }
    resolved = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": file_ref},
    )
    assert resolved.status_code == 200, resolved.text
    descriptor = resolved.json()
    assert descriptor["schemaVersion"] == 1
    assert descriptor["renderer"] == "markdown"
    assert descriptor["capabilities"] == {
        "preview": True,
        "edit": True,
        "download": True,
    }
    assert descriptor["text"] == {
        "encoding": "utf-8",
        "bom": True,
        "newline": "CRLF",
        "mixedNewlines": False,
    }
    assert descriptor["content"]["inlineText"].endswith("Before\r\n")

    initial_events = await _artifact_events(
        app_engine,
        tenant_id=me["tenant_id"],
        scope_id=scope_id,
        path=path,
    )
    assert len(initial_events) == 1
    assert initial_events[0]["event_type"] == "upsert"
    assert (
        vfs_content_revision(initial_events[0]["content_revision"])
        == descriptor["revision"]
    )

    # A VFS read touches last_access for LRU bookkeeping. It must neither
    # change optimistic content identity nor produce a live Preview event.
    read = await client.get(
        "/api/v1/vfs/content",
        headers=headers,
        params={"wf_id": scope_id, "path": path},
    )
    assert read.status_code == 200, read.text
    after_read = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": file_ref},
    )
    assert after_read.status_code == 200, after_read.text
    assert after_read.json()["revision"] == descriptor["revision"]
    assert await _artifact_events(
        app_engine,
        tenant_id=me["tenant_id"],
        scope_id=scope_id,
        path=path,
    ) == initial_events

    saved = await client.put(
        "/api/v1/previews/file",
        headers=headers,
        json={
            "fileRef": file_ref,
            "expectedRevision": descriptor["revision"],
            "contentType": "text/markdown",
            "content": "# Title\n\nAfter\n",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] != descriptor["revision"]
    saved_events = await _artifact_events(
        app_engine,
        tenant_id=me["tenant_id"],
        scope_id=scope_id,
        path=path,
    )
    assert len(saved_events) == 2
    assert saved_events[-1]["event_type"] == "upsert"
    assert (
        vfs_content_revision(saved_events[-1]["content_revision"])
        == saved.json()["revision"]
    )

    stale = await client.put(
        "/api/v1/previews/file",
        headers=headers,
        json={
            "fileRef": file_ref,
            "expectedRevision": descriptor["revision"],
            "contentType": "text/markdown",
            "content": "stale",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "preview_revision_conflict"

    signed = await client.post(
        "/api/v1/vfs/sign",
        headers=headers,
        json={"wf_id": scope_id, "path": path},
    )
    raw = await client.get(signed.json()["url"])
    assert raw.content == b"\xef\xbb\xbf# Title\r\n\r\nAfter\r\n"
    assert raw.headers["etag"].startswith('"sha256:')


@pytest.mark.asyncio
async def test_structured_text_tables_are_preview_only(client, app_engine):
    headers, _me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    path = await _upload(
        client,
        headers,
        scope_id,
        "records.csv",
        b"name,value\nalpha,1\n",
        "text/csv",
    )
    file_ref = {
        "schemaVersion": 1,
        "scope": "chat",
        "chatId": chat_id,
        "path": path,
    }
    resolved = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": file_ref},
    )
    assert resolved.status_code == 200, resolved.text
    descriptor = resolved.json()
    assert descriptor["renderer"] == "spreadsheet"
    assert descriptor["capabilities"] == {
        "preview": True,
        "edit": False,
        "download": True,
    }

    write = await client.put(
        "/api/v1/previews/file",
        headers=headers,
        json={
            "fileRef": file_ref,
            "expectedRevision": descriptor["revision"],
            "contentType": "text/csv",
            "content": "name,value\nbeta,2\n",
        },
    )
    assert write.status_code == 403
    assert write.json()["detail"] == "preview_file_read_only"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/memory/state.md", "/logs/runtime.log"])
async def test_agent_owned_chat_files_are_read_only_previewable(
    client, app_engine, path,
):
    headers, me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    await _write_chat_workspace_file(
        tenant_id=me["tenant_id"],
        scope_id=scope_id,
        path=path,
        data=b"agent-owned context\n",
        mime="text/markdown" if path.endswith(".md") else "text/plain",
    )
    file_ref = {
        "schemaVersion": 1,
        "scope": "chat",
        "chatId": chat_id,
        "path": path,
    }

    resolved = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": file_ref},
    )
    assert resolved.status_code == 200, resolved.text
    descriptor = resolved.json()
    assert descriptor["content"]["inlineText"] == "agent-owned context\n"
    assert descriptor["capabilities"] == {
        "preview": True,
        "edit": False,
        "download": True,
    }

    write = await client.put(
        "/api/v1/previews/file",
        headers=headers,
        json={
            "fileRef": file_ref,
            "expectedRevision": descriptor["revision"],
            "contentType": descriptor["contentType"],
            "content": "user overwrite",
        },
    )
    assert write.status_code == 403
    assert write.json()["detail"] == "preview_file_read_only"


@pytest.mark.asyncio
async def test_preview_detects_pdf_streams_range_and_rejects_plain_archives(
    client, app_engine,
):
    headers, _me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    pdf_path = await _upload(
        client,
        headers,
        scope_id,
        "report.bin",
        b"%PDF-1.7\n" + b"x" * 128,
        "application/octet-stream",
    )
    pdf_ref = {
        "schemaVersion": 1,
        "scope": "chat",
        "chatId": chat_id,
        "path": pdf_path,
    }
    pdf = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": pdf_ref},
    )
    assert pdf.status_code == 200, pdf.text
    assert pdf.json()["renderer"] == "pdf"
    assert pdf.json()["loadPolicy"] == "range"
    ranged = await client.get(
        pdf.json()["content"]["url"],
        headers={"Range": "bytes=0-7"},
    )
    assert ranged.status_code == 206, ranged.text
    assert ranged.content == b"%PDF-1.7"
    assert ranged.headers["content-range"].startswith("bytes 0-7/")

    archive_path = await _upload(
        client,
        headers,
        scope_id,
        "bundle.zip",
        b"PK\x03\x04not-a-preview",
        "application/zip",
    )
    archive = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={
            "fileRef": {
                "schemaVersion": 1,
                "scope": "chat",
                "chatId": chat_id,
                "path": archive_path,
            }
        },
    )
    assert archive.status_code == 200, archive.text
    assert archive.json()["renderer"] == "unsupported"
    assert archive.json()["loadPolicy"] == "unsupported"
    assert archive.json()["error"] == {
        "code": "archive_preview_not_supported",
        "params": {},
    }


@pytest.mark.asyncio
async def test_preview_file_ref_is_owner_scoped(client, app_engine):
    owner_headers, _owner, chat_id, scope_id = await _chat_fixture(client, app_engine)
    path = await _upload(
        client,
        owner_headers,
        scope_id,
        "private.txt",
        b"secret",
        "text/plain",
    )
    other_headers, _other = await _register(client)
    response = await client.post(
        "/api/v1/previews/resolve",
        headers=other_headers,
        json={
            "fileRef": {
                "schemaVersion": 1,
                "scope": "chat",
                "chatId": chat_id,
                "path": path,
            }
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "preview_file_not_found"


@pytest.mark.asyncio
async def test_preview_html_resource_session_maps_workspace_files(
    client, app_engine,
):
    headers, _me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    html_path = await _upload(
        client,
        headers,
        scope_id,
        "index.html",
        b'<img src="/data/pixel.png">',
        "text/html",
    )
    image = b"\x89PNG\r\n\x1a\npreview-resource"
    await _upload(
        client,
        headers,
        scope_id,
        "pixel.png",
        image,
        "image/png",
    )
    await _upload(
        client,
        headers,
        scope_id,
        "private.txt",
        b"not referenced by the HTML",
        "text/plain",
    )
    file_ref = {
        "schemaVersion": 1,
        "scope": "chat",
        "chatId": chat_id,
        "path": html_path,
    }
    response = await client.post(
        "/api/v1/previews/resource-session",
        headers=headers,
        json={"fileRef": file_ref},
    )
    assert response.status_code == 200, response.text
    session = response.json()
    root = next(
        mount["rootUrl"]
        for mount in session["resourceMounts"]
        if mount["pathPrefix"] == "/"
    )
    rendered_resource = await client.get(f"{root}data/pixel.png")
    assert rendered_resource.status_code == 200, rendered_resource.text
    assert rendered_resource.content == image
    unrelated = await client.get(f"{root}data/private.txt")
    assert unrelated.status_code == 403
    wrong_audience = await client.get(
        f"{root}data/pixel.png".replace(
            "/resources/file-preview/",
            "/resources/interactive-artifact/",
        )
    )
    assert wrong_audience.status_code == 403


@pytest.mark.asyncio
async def test_legacy_office_is_download_only_without_conversion(
    client, app_engine,
):
    headers, _me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    path = await _upload(
        client,
        headers,
        scope_id,
        "legacy.rtf",
        b"{\\rtf1\\ansi Preview conversion}",
        "application/rtf",
    )
    file_ref = {
        "schemaVersion": 1,
        "scope": "chat",
        "chatId": chat_id,
        "path": path,
    }
    response = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={"fileRef": file_ref},
    )
    assert response.status_code == 200, response.text
    descriptor = response.json()
    assert descriptor["renderer"] == "unsupported"
    assert descriptor["loadPolicy"] == "unsupported"
    assert descriptor["capabilities"]["download"] is True
    assert descriptor["error"] == {
        "code": "unsupported_file_type",
        "params": {"extension": ".rtf"},
    }


@pytest.mark.asyncio
async def test_large_table_returns_structured_preview_error(client, app_engine):
    headers, _me, chat_id, scope_id = await _chat_fixture(client, app_engine)
    data = b"name,value\n" + b"a,1\n" * (3 * 1024 * 1024)
    assert len(data) > 10 * 1024 * 1024
    path = await _upload(
        client,
        headers,
        scope_id,
        "large.csv",
        data,
        "text/csv",
    )
    response = await client.post(
        "/api/v1/previews/resolve",
        headers=headers,
        json={
            "fileRef": {
                "schemaVersion": 1,
                "scope": "chat",
                "chatId": chat_id,
                "path": path,
            }
        },
    )
    assert response.status_code == 200, response.text
    descriptor = response.json()
    assert descriptor["renderer"] == "unsupported"
    assert descriptor["loadPolicy"] == "unsupported"
    assert descriptor["error"]["code"] == "file_too_large"
    assert descriptor["error"]["params"]["actualBytes"] == len(data)
    assert descriptor["error"]["params"]["limitBytes"] == 10 * 1024 * 1024

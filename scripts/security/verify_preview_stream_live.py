#!/usr/bin/env python3
"""Exercise Preview Range and durable file-event replay against live services.

The verifier uses only browser-facing Cookie-mode APIs.  It creates an
ordinary chat by uploading an attachment, resolves the resulting FileRef,
measures bounded byte-range reads, then proves ordered Last-Event-ID replay
after two writes made while the subscriber is disconnected.  A second user is
also denied both descriptor and event-stream access to the FileRef.
"""
from __future__ import annotations

import argparse
import base64
from collections.abc import Iterator
import json
import math
import statistics
import time
import uuid

import httpx


def _csrf(client: httpx.Client) -> str:
    for cookie in client.cookies.jar:
        if cookie.name.endswith("vibecanvas-web-csrf"):
            return cookie.value
    raise AssertionError("browser CSRF cookie was not issued")


def _headers(
    client: httpx.Client,
    web_origin: str,
    *,
    mutate: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {"Origin": web_origin, **(extra or {})}
    if mutate:
        headers["X-CSRF-Token"] = _csrf(client)
    return headers


def _ok(response: httpx.Response, operation: str) -> httpx.Response:
    if response.status_code >= 400:
        raise AssertionError(
            f"{operation} failed: {response.status_code} {response.text}"
        )
    return response


def _register(api_url: str, web_origin: str, label: str) -> httpx.Client:
    client = httpx.Client(base_url=api_url, timeout=30)
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/auth/register",
        headers={"Origin": web_origin},
        json={
            "email": f"preview-live-{label}-{suffix}@example.com",
            "username": f"preview-{label}",
            "password": "Preview-live-42!",
        },
    )
    _ok(response, "register")
    return client


def _percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _sse_events(response: httpx.Response) -> Iterator[dict[str, object]]:
    event_type = "message"
    event_id = ""
    data: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if data:
                payload = json.loads("\n".join(data))
                if not isinstance(payload, dict):
                    raise AssertionError("SSE data was not a JSON object")
                yield {"event": event_type, "id": event_id, "data": payload}
            event_type = "message"
            event_id = ""
            data = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data.append(value)


def _read_events(
    client: httpx.Client,
    *,
    path: str,
    chat_id: str,
    web_origin: str,
    last_event_id: int | None,
    count: int,
) -> tuple[list[dict[str, object]], float]:
    headers = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    started = time.perf_counter()
    with client.stream(
        "GET",
        "/api/v1/previews/events",
        params={"scope": "chat", "chat_id": chat_id, "path": path},
        headers=_headers(client, web_origin, extra=headers),
        timeout=15,
    ) as response:
        _ok(response, "open preview event stream")
        events = []
        for event in _sse_events(response):
            events.append(event)
            if len(events) == count:
                return events, (time.perf_counter() - started) * 1000
    raise AssertionError(f"preview event stream ended before {count} frames")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-origin", default="http://127.0.0.1:9001")
    parser.add_argument("--range-samples", type=int, default=100)
    args = parser.parse_args()
    if args.range_samples < 20:
        raise SystemExit("--range-samples must be at least 20")

    owner = _register(args.api_url, args.web_origin, "owner")
    intruder = _register(args.api_url, args.web_origin, "intruder")
    try:
        bootstrap = _ok(
            owner.get(
                "/api/v1/chats/bootstrap",
                params={"surface": "chat"},
                headers=_headers(owner, args.web_origin),
            ),
            "bootstrap chat",
        ).json()
        carrier_scope_id = str(bootstrap["carrier_scope_id"])
        chat_id = f"preview_live_{uuid.uuid4().hex[:16]}"
        payload = b"%PDF-1.7\n" + bytes(range(256)) * 32_768
        uploaded = _ok(
            owner.post(
                f"/api/v1/chat-scopes/{carrier_scope_id}/chats/{chat_id}/attachments",
                params={"attachment_type": "file"},
                headers=_headers(owner, args.web_origin, mutate=True),
                files={"file": ("range-report.bin", payload, "application/octet-stream")},
            ),
            "upload preview fixture",
        ).json()
        path = str(uploaded["path"])
        workspace = _ok(
            owner.get(
                "/api/v1/chats/workspace",
                params={"chat_id": chat_id},
                headers=_headers(owner, args.web_origin),
            ),
            "resolve chat workspace",
        ).json()
        workspace_scope_id = str(workspace["workspace_scope_id"])
        file_ref = {
            "schemaVersion": 1,
            "scope": "chat",
            "chatId": chat_id,
            "path": path,
        }

        resolve_started = time.perf_counter()
        descriptor = _ok(
            owner.post(
                "/api/v1/previews/resolve",
                headers=_headers(owner, args.web_origin, mutate=True),
                json={"fileRef": file_ref},
            ),
            "resolve preview",
        ).json()
        resolve_ms = (time.perf_counter() - resolve_started) * 1000
        assert descriptor["renderer"] == "pdf"
        assert descriptor["loadPolicy"] == "range"
        assert descriptor["content"]["rangeSupported"] is True
        content_url = str(descriptor["content"]["url"])

        text_source = (
            b"\xef\xbb\xbf# Preview gate\r\n\r\n"
            b"[Open documentation](https://example.com/docs)\r\n"
        )
        text_upload = _ok(
            owner.post(
                f"/api/v1/chat-scopes/{carrier_scope_id}/chats/{chat_id}/attachments",
                params={"attachment_type": "file"},
                headers=_headers(owner, args.web_origin, mutate=True),
                files={"file": ("editable-notes.md", text_source, "text/markdown")},
            ),
            "upload editable Preview fixture",
        ).json()
        text_path = str(text_upload["path"])
        text_ref = {
            "schemaVersion": 1,
            "scope": "chat",
            "chatId": chat_id,
            "path": text_path,
        }
        text_descriptor = _ok(
            owner.post(
                "/api/v1/previews/resolve",
                headers=_headers(owner, args.web_origin, mutate=True),
                json={"fileRef": text_ref},
            ),
            "resolve editable Preview",
        ).json()
        assert text_descriptor["renderer"] == "markdown"
        assert text_descriptor["capabilities"]["edit"] is True
        assert text_descriptor["text"] == {
            "encoding": "utf-8",
            "bom": True,
            "newline": "CRLF",
            "mixedNewlines": False,
        }
        saved_text = "# Preview gate\n\nSaved through Preview.\n"
        saved = _ok(
            owner.put(
                "/api/v1/previews/file",
                headers=_headers(owner, args.web_origin, mutate=True),
                json={
                    "fileRef": text_ref,
                    "expectedRevision": text_descriptor["revision"],
                    "contentType": "text/markdown",
                    "content": saved_text,
                },
            ),
            "save Preview text",
        ).json()
        assert saved["revision"] != text_descriptor["revision"]
        stale_save = owner.put(
            "/api/v1/previews/file",
            headers=_headers(owner, args.web_origin, mutate=True),
            json={
                "fileRef": text_ref,
                "expectedRevision": text_descriptor["revision"],
                "contentType": "text/markdown",
                "content": "stale overwrite",
            },
        )
        assert stale_save.status_code == 409
        assert stale_save.json()["detail"] == "preview_revision_conflict"
        text_after_save = _ok(
            owner.post(
                "/api/v1/previews/resolve",
                headers=_headers(owner, args.web_origin, mutate=True),
                json={"fileRef": text_ref},
            ),
            "resolve saved Preview",
        ).json()
        assert text_after_save["content"]["inlineText"] == (
            "# Preview gate\r\n\r\nSaved through Preview.\r\n"
        )
        assert text_after_save["text"]["bom"] is True

        invalid_external_ref = owner.post(
            "/api/v1/previews/resolve",
            headers=_headers(owner, args.web_origin, mutate=True),
            json={
                "fileRef": {
                    "schemaVersion": 1,
                    "scope": "chat",
                    "chatId": chat_id,
                    "path": "https://attacker.invalid/implicit-proxy",
                }
            },
        )
        assert invalid_external_ref.status_code == 422

        range_samples: list[float] = []
        for sample_index in range(args.range_samples):
            start = (sample_index * 4_096) % (len(payload) - 4_096)
            before = time.perf_counter()
            response = owner.get(
                content_url,
                headers={"Range": f"bytes={start}-{start + 4_095}"},
            )
            range_samples.append((time.perf_counter() - before) * 1000)
            assert response.status_code == 206, response.text
            assert response.content == payload[start : start + 4_096]
            assert response.headers["content-range"] == (
                f"bytes {start}-{start + 4_095}/{len(payload)}"
            )
            assert response.headers["accept-ranges"] == "bytes"

        initial, ready_ms = _read_events(
            owner,
            path=path,
            chat_id=chat_id,
            web_origin=args.web_origin,
            last_event_id=None,
            count=1,
        )
        assert initial[0]["event"] == "preview_ready"
        initial_cursor = int(str(initial[0]["id"]))
        assert initial_cursor > 0

        revisions: list[str] = []
        for marker in (b"first", b"second"):
            updated = payload[:-len(marker)] + marker
            response = owner.put(
                "/api/v1/vfs/bytes",
                headers=_headers(owner, args.web_origin, mutate=True),
                json={
                    "wf_id": workspace_scope_id,
                    "path": path,
                    "data_b64": base64.b64encode(updated).decode("ascii"),
                    "content_type": "application/pdf",
                },
            )
            _ok(response, "write preview update")
            resolved = _ok(
                owner.post(
                    "/api/v1/previews/resolve",
                    headers=_headers(owner, args.web_origin, mutate=True),
                    json={"fileRef": file_ref},
                ),
                "resolve updated preview",
            ).json()
            revisions.append(str(resolved["revision"]))

        replay, replay_ms = _read_events(
            owner,
            path=path,
            chat_id=chat_id,
            web_origin=args.web_origin,
            last_event_id=initial_cursor,
            count=3,
        )
        assert [event["event"] for event in replay] == [
            "preview_ready",
            "preview_file",
            "preview_file",
        ]
        replay_ids = [int(str(event["id"])) for event in replay]
        assert replay_ids[0] == initial_cursor
        assert replay_ids[1:] == sorted(replay_ids[1:])
        assert replay_ids[1] > initial_cursor
        replay_revisions = [
            str(event["data"]["revision"]) for event in replay[1:]
        ]
        assert replay_revisions == revisions

        denied_resolve = intruder.post(
            "/api/v1/previews/resolve",
            headers=_headers(intruder, args.web_origin, mutate=True),
            json={"fileRef": file_ref},
        )
        assert denied_resolve.status_code in {403, 404}
        denied_stream = intruder.get(
            "/api/v1/previews/events",
            params={"scope": "chat", "chat_id": chat_id, "path": path},
            headers=_headers(
                intruder,
                args.web_origin,
                extra={"Accept": "text/event-stream"},
            ),
        )
        assert denied_stream.status_code in {403, 404}

        mean_ms = statistics.fmean(range_samples)
        print(
            "preview_stream_gate=pass "
            f"bytes={len(payload)} ranges={len(range_samples)} "
            f"resolve_ms={resolve_ms:.3f} ready_ms={ready_ms:.3f} "
            f"replay_2_ms={replay_ms:.3f} "
            f"range_mean_ms={mean_ms:.3f} "
            f"range_p50_ms={_percentile(range_samples, 0.50):.3f} "
            f"range_p95_ms={_percentile(range_samples, 0.95):.3f} "
            f"range_p99_ms={_percentile(range_samples, 0.99):.3f} "
            "save=pass conflict=pass external_proxy=denied cross_user=denied"
        )
        return 0
    finally:
        owner.close()
        intruder.close()


if __name__ == "__main__":
    raise SystemExit(main())

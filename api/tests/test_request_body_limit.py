from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI, Request

from vibecanvas_api.request_body_limit import RequestBodyLimitMiddleware


async def _call(*, path: str, chunks: list[bytes], headers=(), limit: int = 8):
    downstream_bodies: list[bytes] = []
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    async def app(_scope, downstream_receive, send):
        while True:
            message = await downstream_receive()
            downstream_bodies.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = []

    async def send(message):
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(app, default_limit=limit)
    await middleware(
        {"type": "http", "path": path, "headers": list(headers)},
        receive,
        send,
    )
    return sent, b"".join(downstream_bodies)


def test_rejects_actual_streamed_bytes_when_content_length_is_missing():
    sent, downstream_body = asyncio.run(
        _call(path="/api/v1/uploads", chunks=[b"12345", b"6789"])
    )
    assert sent[0]["status"] == 413
    # The parser may consume bounded prefix chunks, but it never receives a
    # complete body and therefore cannot enter the route handler.
    assert downstream_body == b"12345"


def test_rejects_oversized_declared_length_without_reading_body():
    sent, downstream_body = asyncio.run(
        _call(
            path="/api/v1/uploads",
            chunks=[b"small"],
            headers=((b"content-length", b"9"),),
        )
    )
    assert sent[0]["status"] == 413
    assert downstream_body == b""


def test_replays_body_to_downstream_in_order():
    sent, downstream_body = asyncio.run(
        _call(path="/api/v1/uploads", chunks=[b"123", b"45678"])
    )
    assert sent[0]["status"] == 204
    assert downstream_body == b"12345678"


def test_public_deployment_endpoints_use_stricter_one_mebibyte_limit():
    for suffix in ("invoke", "webhook"):
        sent, _downstream_body = asyncio.run(
            _call(
                path=f"/api/v1/deployments/public-hook/{suffix}",
                chunks=[b""],
                headers=(
                    (b"content-length", str(1024 * 1024 + 1).encode()),
                ),
                limit=64 * 1024 * 1024,
            )
        )
        assert sent[0]["status"] == 413


def test_fastapi_body_parser_maps_stream_overflow_to_413():
    app = FastAPI()

    @app.post("/upload")
    async def upload(request: Request):
        return {"size": len(await request.body())}

    limited_app = RequestBodyLimitMiddleware(app, default_limit=8)

    async def request():
        transport = httpx.ASGITransport(app=limited_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://test.invalid",
        ) as client:
            return await client.post("/upload", content=b"123456789")

    response = asyncio.run(request())
    assert response.status_code == 413
    assert response.json()["limit_bytes"] == 8

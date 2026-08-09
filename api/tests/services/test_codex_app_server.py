from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vibecanvas_api.services.agent_runtime.codex_app_server import (
    CodexAppServer,
    CodexAppServerError,
)


@pytest.mark.asyncio
async def test_reader_accepts_resume_records_larger_than_asyncio_default() -> None:
    server = CodexAppServer(
        executable="/codex",
        env={},
        read_limit_bytes=256 * 1024,
    )
    reader = asyncio.StreamReader(limit=server._read_limit_bytes)
    server._process = SimpleNamespace(stdout=reader)
    payload = {
        "method": "thread/resumed",
        "params": {"history": "x" * (96 * 1024)},
    }
    reader.feed_data((json.dumps(payload) + "\n").encode())
    reader.feed_eof()

    await server._read_loop()
    messages = server.messages()
    message = await anext(messages)

    assert message == payload
    with pytest.raises(CodexAppServerError, match="closed its output stream"):
        await anext(messages)


def test_reader_limit_rejects_invalid_environment(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_APP_SERVER_JSONL_LIMIT_BYTES", "not-an-integer")

    with pytest.raises(
        RuntimeError,
        match="CODEX_APP_SERVER_JSONL_LIMIT_BYTES must be an integer",
    ):
        CodexAppServer(executable="/codex", env={})


@pytest.mark.asyncio
async def test_server_request_error_response_is_bounded() -> None:
    server = CodexAppServer(executable="/codex", env={})
    server._send = AsyncMock()

    await server.respond_error(
        91,
        code=-32601,
        message="x" * 600,
    )

    server._send.assert_awaited_once_with({
        "id": 91,
        "error": {"code": -32601, "message": "x" * 500},
    })


@pytest.mark.asyncio
async def test_outer_sandboxed_server_disables_redundant_codex_sandbox(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeStdin:
        def write(self, _payload: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

    async def create_subprocess(*arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            stdin=FakeStdin(),
            stdout=object(),
            returncode=None,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    server = CodexAppServer(
        executable="/codex",
        env={},
        outer_sandboxed=True,
    )
    server._read_loop = AsyncMock()
    server.request = AsyncMock(return_value={})
    server.notify = AsyncMock()

    await server.start()

    assert captured["arguments"] == (
        "/codex",
        "-c",
        'cli_auth_credentials_store="file"',
        "-c",
        'sandbox_mode="danger-full-access"',
        "app-server",
        "--stdio",
    )
    server.request.assert_awaited_once()
    server.notify.assert_awaited_once_with("initialized", {})

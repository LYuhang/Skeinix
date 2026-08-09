"""MCP management probes always execute inside the configured sandbox."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from vibecanvas_api.services.mcp_handshake import handshake_one


@pytest.mark.asyncio
async def test_handshake_one_returns_sandbox_tool_manifest():
    class ProbeProvider:
        request = None

        def run_mcp_probe(self, *, request, timeout, allow_hosts):
            self.request = request
            assert timeout == 5.0
            assert allow_hosts == {"x.example"}
            return {
                "status": "ok",
                "tool_count": 2,
                "tool_names": [
                    {"name": "a", "description": "stub tool"},
                    {"name": "b", "description": "desc-b"},
                ],
            }

    provider = ProbeProvider()
    with (
        patch(
            "vibecanvas_api.services.mcp_handshake.validate_mcp_connection_destination",
            new_callable=AsyncMock,
        ) as validate,
        patch(
            "vibecanvas_api.services.mcp_handshake.get_sandbox_provider",
            return_value=provider,
        ),
    ):
        validate.return_value = {"x.example"}
        result = await handshake_one(
            prefix="px",
            transport="sse",
            endpoint="https://x.example/sse",
            auth_config={},
            timeout_s=5.0,
        )

    assert result["status"] == "ok"
    assert result["tool_count"] == 2
    assert result["tools"] == []
    assert provider.request["connection"] == {
        "transport": "sse",
        "url": "https://x.example/sse",
    }


@pytest.mark.asyncio
async def test_handshake_one_preserves_sandbox_timeout_error():
    class ProbeProvider:
        def run_mcp_probe(self, **_kwargs):
            return {
                "status": "error: handshake timed out after 0.05s",
                "tool_count": None,
                "tool_names": None,
            }

    with (
        patch(
            "vibecanvas_api.services.mcp_handshake.validate_mcp_connection_destination",
            new_callable=AsyncMock,
            return_value={"slow.example"},
        ),
        patch(
            "vibecanvas_api.services.mcp_handshake.get_sandbox_provider",
            return_value=ProbeProvider(),
        ),
    ):
        result = await handshake_one(
            prefix="slow",
            transport="sse",
            endpoint="https://slow.example/sse",
            auth_config={},
            timeout_s=0.05,
        )

    assert result["status"].startswith("error: handshake timed out")
    assert result["tools"] == []


@pytest.mark.asyncio
async def test_handshake_one_captures_sandbox_infrastructure_failure():
    class ProbeProvider:
        def run_mcp_probe(self, **_kwargs):
            raise ConnectionError("boom")

    with (
        patch(
            "vibecanvas_api.services.mcp_handshake.validate_mcp_connection_destination",
            new_callable=AsyncMock,
            return_value={"bad.example"},
        ),
        patch(
            "vibecanvas_api.services.mcp_handshake.get_sandbox_provider",
            return_value=ProbeProvider(),
        ),
    ):
        result = await handshake_one(
            prefix="bad",
            transport="sse",
            endpoint="https://bad.example/sse",
            auth_config={},
            timeout_s=5.0,
        )

    assert result["status"].startswith("error:")
    assert "boom" in result["status"]
    assert result["tools"] == []


@pytest.mark.asyncio
async def test_stdio_handshake_has_no_remote_allow_hosts():
    class ProbeProvider:
        def run_mcp_probe(self, *, request, timeout, allow_hosts):
            assert request["connection"] == {
                "transport": "stdio",
                "command": "sandbox-command",
                "args": ["--serve"],
            }
            assert allow_hosts == set()
            return {"status": "ok", "tool_count": 0, "tool_names": []}

    with (
        patch(
            "vibecanvas_api.services.mcp_handshake.validate_mcp_connection_destination",
            new_callable=AsyncMock,
            return_value=set(),
        ),
        patch(
            "vibecanvas_api.services.mcp_handshake.get_sandbox_provider",
            return_value=ProbeProvider(),
        ),
    ):
        result = await handshake_one(
            prefix="local",
            transport="stdio",
            endpoint="sandbox-command",
            auth_config={},
            connection_config={"args": ["--serve"]},
            timeout_s=5.0,
        )

    assert result["status"] == "ok"

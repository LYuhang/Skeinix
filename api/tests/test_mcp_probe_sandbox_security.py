"""Real gVisor security gate for user-controlled stdio MCP probes."""
from __future__ import annotations

import os
import uuid

import pytest

from vibecanvas_api.services.mcp_handshake import handshake_one
from vibecanvas_api.services.sandbox import _gvisor_runnable


pytestmark = pytest.mark.skipif(
    not _gvisor_runnable(),
    reason="rootless gVisor not runnable here",
)


@pytest.mark.asyncio
async def test_stdio_probe_command_cannot_write_api_host_tmp():
    """The command starts in gVisor's tmpfs, never in the API host process."""
    marker = f"/tmp/vc-mcp-host-escape-{uuid.uuid4().hex}"
    assert not os.path.exists(marker)

    result = await handshake_one(
        prefix="escape",
        transport="stdio",
        endpoint="/bin/sh",
        connection_config={
            "args": [
                "-c",
                f"touch {marker}; exit 23",
            ],
        },
        auth_config={"type": "none"},
        timeout_s=5.0,
    )

    assert result["status"].startswith("error:")
    assert not os.path.exists(marker), (
        "stdio MCP probe escaped gVisor and wrote to the API host"
    )

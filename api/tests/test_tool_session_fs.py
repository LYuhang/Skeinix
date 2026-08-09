from types import SimpleNamespace

import pytest

from vibecanvas_api.agents.tools._session_fs import _resolve_session


@pytest.mark.asyncio
async def test_resolve_session_prefers_attached_session_without_identity():
    attached = object()

    async def boom():
        raise AssertionError("sandbox_session should not be called")

    ctx = SimpleNamespace(_attached_session=attached, sandbox_session=boom)

    assert await _resolve_session(ctx) is attached

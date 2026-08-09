from __future__ import annotations

import pytest

from vibecanvas_api.services.agent_runtime.local_session import LocalAgentRuntimeSession


@pytest.mark.asyncio
async def test_local_runtime_session_restricts_roots_and_round_trips(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    session = LocalAgentRuntimeSession([str(root)])
    target = root / "sample.txt"

    assert (await session.write_file(str(target), "one\ntwo\n"))["ok"] is True
    result = await session.read_file(str(target))
    assert result == {"ok": True, "kind": "text", "content": "one\ntwo\n"}

    outside = await session.read_file(str(tmp_path / "private" / "auth.json"))
    assert outside == {"ok": False, "error": "path_outside_roots"}


@pytest.mark.asyncio
async def test_local_runtime_session_edit_contract(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    target = root / "sample.txt"
    target.write_text("alpha beta", encoding="utf-8")
    session = LocalAgentRuntimeSession([str(root)])

    result = await session.edit_file(str(target), "beta", "gamma")
    assert result["ok"] is True
    assert result["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "alpha gamma"

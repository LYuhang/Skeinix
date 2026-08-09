from __future__ import annotations

import pytest

from vibecanvas_api.agents.tools import workspace_fs


@pytest.mark.asyncio
async def test_skill_mount_is_readable_but_never_a_write_root(monkeypatch):
    monkeypatch.setenv("VIBECANVAS_AGENT_RUNTIME_IN_SANDBOX", "1")
    monkeypatch.setattr(workspace_fs.os.path, "isdir", lambda _path: True)
    calls: list[tuple[dict, list[str]]] = []

    def fake_fileop(request, roots):
        calls.append((request, roots))
        return {"ok": True, "kind": "text", "content": "skill"}

    monkeypatch.setattr(workspace_fs, "run_fileop", fake_fileop)

    await workspace_fs.read_file("/skills/skill-1/SKILL.md")
    await workspace_fs.write_file("/skills/skill-1/SKILL.md", "changed")

    assert "/skills" in calls[0][1]
    assert "/skills" not in calls[1][1]

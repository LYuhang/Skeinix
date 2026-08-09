from unittest.mock import AsyncMock, MagicMock

import pytest

from vibecanvas_api.agents.tools import workspace_fs
from vibecanvas_api.agents.tools.sandbox.bash import _bash


@pytest.mark.asyncio
async def test_run_command_runs_shell_in_run_and_writes_back(monkeypatch):
    from vibecanvas_api.services.sandbox.manager import SandboxSession
    captured = {}

    async def fake_submit_fileop(op, *, timeout=30.0):
        captured["op"] = op
        captured["timeout"] = timeout
        return {"ok": True, "stdout": "hi\n", "stderr": "", "exit_code": 0}

    sess = SandboxSession(
        tenant_id="t",
        wf_id="w",
        run_dir="/tmp/x",
        overlay_dir="/tmp/o",
        provider=MagicMock(),
        base_binds=[],
    )
    monkeypatch.setattr(sess, "_submit_fileop", fake_submit_fileop)
    wb = AsyncMock()
    monkeypatch.setattr(sess, "writeback_vfs", wb)
    out = await sess.run_command("echo hi", timeout_s=30)
    assert out["exit_code"] == 0
    assert captured["op"] == {
        "op": "exec",
        "command": "echo hi",
        "cwd": "/",
        "timeout": 30.0,
    }
    assert captured["timeout"] == 40.0
    wb.assert_awaited_once()


@pytest.mark.asyncio
async def test_bash_content_is_terminal_output(monkeypatch):
    async def _run(_command, *, timeout_s=60):
        return {"stdout": "hi\n", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(workspace_fs, "run_command", _run)
    content, art = await _bash(command="echo hi", timeout_s=60)
    assert content == "hi\n"                                  # terminal stdout in content
    assert art["status"] == "success"
    assert art["artifact"]["handles"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_bash_workspace_unavailable_error_envelope(monkeypatch):
    async def _fail(_command, *, timeout_s=60):
        raise RuntimeError("internal workspace detail")

    monkeypatch.setattr(workspace_fs, "run_command", _fail)
    content, art = await _bash(command="echo hi", timeout_s=60)
    assert art["status"] == "error"
    assert art["error"]["code"] == "run_failed"
    assert "internal workspace detail" not in content

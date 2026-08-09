"""§4.1a + §7 — wire `shell` (run_command) and `read_file` (read_path) to the
tiered large-output compaction (fill_output_data).

A LARGE shell stdout / file body must NOT blind-truncate inline: the FULL body is
written to the VFS, the envelope carries `output.path` + `output.full_chars/
full_tokens` (no inline `data`), so the compaction middleware re-hydrates the
head+tail tier from VFS. A SMALL body keeps the old inline UX.
"""
import pytest

from vibecanvas_api.agents.tools.sandbox.bash import _bash
from vibecanvas_api.agents.tools.fs.read_file import _read_workspace
from vibecanvas_api.agents.tools import workspace_fs


def _workspace_reading(content, kind="text"):
    """A Runtime-local workspace reader returning an in-sandbox fileop dict."""
    async def _rf(path):
        return {"ok": True, "kind": kind, "content": content}
    return _rf


# --------------------------------------------------------------------------- #
# Runtime-local command harness (no Postgres or sandbox process)              #
# --------------------------------------------------------------------------- #
def _command_result(stdout="", stderr="", exit_code=0):
    async def _run(_command, *, timeout_s=60):
        return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}

    return _run


# --------------------------------------------------------------------------- #
# Target 1 — shell (bash): content = terminal stdout; large output offloaded    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_shell_small_stdout_is_terminal_content(monkeypatch):
    """Small output → content IS the terminal stdout (not a JSON envelope); the
    exit_code rides the artifact handle."""
    monkeypatch.setattr(
        workspace_fs, "run_command", _command_result("hello\nworld\n")
    )
    content, art = await _bash(command="echo hi", timeout_s=60)
    assert content == "hello\nworld\n"                    # terminal output, full
    assert art["status"] == "success"
    assert art["artifact"]["handles"]["exit_code"] == 0
    assert art["artifact"]["target"] == {}                # small → not offloaded


@pytest.mark.asyncio
async def test_shell_large_stdout_is_guardrail_truncated(monkeypatch):
    """Without a host AgentContext inside the Runtime process, a large output is
    still bounded safely; the artifact truthfully reports that there is no ref."""
    big = "x" * 40000                                    # > inline_chars
    monkeypatch.setattr(workspace_fs, "run_command", _command_result(big))
    content, art = await _bash(command="cat big.txt", timeout_s=60)
    assert len(content) < len(big)                         # truncated preview, not full
    assert "full content not stored" in content
    assert art["artifact"]["target"] == {}
    assert art["artifact"]["handles"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_shell_stderr_in_content_and_exit_code_in_handle(monkeypatch):
    """A non-zero exit is still a successful tool call: stdout + a [stderr] section
    are in the terminal content; the exit_code rides the artifact handle."""
    monkeypatch.setattr(
        workspace_fs, "run_command", _command_result("partial", "boom", 2)
    )
    content, art = await _bash(command="false", timeout_s=60)
    assert "partial" in content and "boom" in content      # stdout + [stderr]
    assert art["status"] == "success"                      # the TOOL call succeeded
    assert art["artifact"]["handles"]["exit_code"] == 2


@pytest.mark.asyncio
async def test_shell_runtime_large_truncates_without_ref(monkeypatch):
    """A large output still truncates (guardrail) but with no host VFS ref,
    never crashes."""
    monkeypatch.setattr(
        workspace_fs, "run_command", _command_result("y" * 50000)
    )
    content, art = await _bash(command="echo hi", timeout_s=60)
    assert len(content) < 50000                            # truncated preview
    assert art["artifact"]["target"] == {}                 # no host VFS → no offload ref


@pytest.mark.asyncio
async def test_shell_no_workspace_error_envelope(monkeypatch):
    async def _fail(_command, *, timeout_s=60):
        raise RuntimeError("internal sandbox detail")

    monkeypatch.setattr(workspace_fs, "run_command", _fail)
    content, art = await _bash(command="echo hi", timeout_s=60)
    assert art["status"] == "error"
    assert art["error"]["code"] == "run_failed"
    assert "internal sandbox detail" not in content


@pytest.mark.asyncio
async def test_read_file_small_text_unchanged(monkeypatch):
    """Small text → raw content (the text itself, not an envelope)."""
    monkeypatch.setattr(
        "vibecanvas_api.agents.tools.workspace_fs.read_file",
        _workspace_reading("hello small"),
    )
    content, art = await _read_workspace("/data/x_1.txt", 0, 0)
    assert "hello small" in content
    assert art["meta"]["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_read_file_large_text_returned_in_full(monkeypatch):
    """read_file is the VIEWER (is_viewer): large text comes back in FULL — no
    guardrail truncation — with its (path-derived) content_type on the artifact."""
    big = "z" * 40000
    monkeypatch.setattr(
        "vibecanvas_api.agents.tools.workspace_fs.read_file",
        _workspace_reading(big),
    )
    content, art = await _read_workspace("/data/x_1.md", 0, 0)
    assert content == big                                # full body, not a preview
    assert art["meta"]["content_type"] == "text/markdown"        # derived from the .md path
    assert art["artifact"]["target"]["path"] == "/data/x_1.md"   # the path it read


@pytest.mark.asyncio
async def test_read_file_large_text_default_content_type(monkeypatch):
    """An extension-less / unknown path → default text/plain on the artifact."""
    big = "q" * 40000
    monkeypatch.setattr(
        "vibecanvas_api.agents.tools.workspace_fs.read_file",
        _workspace_reading(big),
    )
    _content, art = await _read_workspace("/data/x_1.txt", 0, 0)
    assert art["meta"]["content_type"] == "text/plain"

"""Tests for the single-backend fs flag (Task 2 + Task 3 + Task 4).

Harness helpers at the top; existing Task 2 tests follow, then Task 3 read tests,
then Task 4 write tests.
"""
import types
import pytest

from vibecanvas_api.agents.tools._session_fs import _require_session
from vibecanvas_api.agents.tools.decorator import ToolError


# ---------------------------------------------------------------------------
# Task 2 — _require_session tests (unchanged)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_require_session_raises_without_accessor():
    ctx = types.SimpleNamespace()           # no sandbox_session accessor
    with pytest.raises(ToolError):
        await _require_session(ctx)


@pytest.mark.asyncio
async def test_require_session_returns_session():
    sentinel = object()
    async def _get(): return sentinel
    ctx = types.SimpleNamespace(sandbox_session=_get)
    assert await _require_session(ctx) is sentinel


# ---------------------------------------------------------------------------
# Task 3 — read_file uses the Runtime-local workspace filesystem. The Runtime
# process is already inside the sandbox, so no SandboxSession is passed through.
# ---------------------------------------------------------------------------

async def _call_read_workspace(monkeypatch, path, result, offset=0, limit=0):
    """Drive the current Runtime-local read seam without a real sandbox."""
    from vibecanvas_api.agents.tools import workspace_fs
    from vibecanvas_api.agents.tools.fs.read_file import _read_workspace

    async def _rf(_path):
        return result

    monkeypatch.setattr(workspace_fs, "read_file", _rf)
    return await _read_workspace(path, offset, limit)


@pytest.mark.asyncio
async def test_single_backend_reads_session_file(monkeypatch):
    """The read comes from the Runtime-local workspace; VFS is never touched."""
    result = {"ok": True, "kind": "text", "content": "hello from session"}
    content, art = await _call_read_workspace(monkeypatch, "/data/x.txt", result)
    assert "hello from session" in content
    assert art["artifact"]["target"]["path"] == "/data/x.txt"


@pytest.mark.asyncio
async def test_single_backend_missing_file_raises_path_not_found(monkeypatch):
    """Flag on: the sandbox reports not_found → path_not_found (no VFS fallback)."""
    result = {"ok": False, "error": "not_found"}
    content, art = await _call_read_workspace(monkeypatch, "/data/missing.txt", result)
    # The ToolError is caught by @tool_output → error envelope.
    assert art["status"] == "error"
    assert art["error"]["code"] == "path_not_found"
    assert "does not exist" in content


@pytest.mark.asyncio
async def test_single_backend_traversal_raises_invalid_path(monkeypatch):
    """Flag on: containment now lives IN the sandbox — an out-of-roots path comes
    back as ``path_outside_roots``, which the tool maps to ToolError("invalid_path")."""
    result = {"ok": False, "error": "path_outside_roots"}
    content, art = await _call_read_workspace(
        monkeypatch, "/data/../../etc/passwd", result
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "invalid_path"
    assert "outside" in content                    # the human explanation reaches the agent


# ---------------------------------------------------------------------------
# write_file single-backend → Runtime-local workspace_fs.write_file.
# _write_workspace is @tool_output → returns a (content, artifact) tuple and captures
# a raised ToolError into an error envelope (so assert on the tuple). The old
# _sync_write_path single-backend branch is gone — write AND edit both go
# in-sandbox now, so there is no host-mount write path left to test here.
# ---------------------------------------------------------------------------

def _session_with_write(result=None, raise_runtime=False):
    """A single-backend session stub whose async ``write_file`` returns ``result``
    (an in-sandbox fileop dict), or raises an INTERNAL RuntimeError to prove it is
    not leaked to the agent."""
    async def _wf(path, content):
        if raise_runtime:
            raise RuntimeError("no sandbox for this session (run_dir is None — "
                               "InMemory / no workflow); the warm file API is unavailable")
        return result
    return types.SimpleNamespace(write_file=_wf)


async def _call_write_workspace(monkeypatch, path, content, session):
    """Drive the current Runtime-local write seam without a real sandbox."""
    from vibecanvas_api.agents.tools import workspace_fs
    from vibecanvas_api.agents.tools.fs.write_file import _write_workspace

    monkeypatch.setattr(workspace_fs, "write_file", session.write_file)
    return await _write_workspace(path, content)


@pytest.mark.asyncio
async def test_write_session_writes_in_sandbox(monkeypatch):
    """Flag on: the write goes to the SANDBOX (session.write_file); clean ok envelope."""
    session = _session_with_write({"ok": True, "bytes": 4})
    content, art = await _call_write_workspace(
        monkeypatch, "/data/y.txt", "body", session
    )
    assert art["status"] == "success"
    assert content == "ok"
    assert art["artifact"]["handles"]["path"] == "/data/y.txt"


@pytest.mark.asyncio
async def test_write_session_outside_roots_is_invalid_path(monkeypatch):
    """Containment lives IN the sandbox → path_outside_roots maps to invalid_path."""
    session = _session_with_write({"ok": False, "error": "path_outside_roots"})
    content, art = await _call_write_workspace(
        monkeypatch, "/data/../../etc/passwd", "evil", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "invalid_path"
    assert "outside" in content


@pytest.mark.asyncio
async def test_write_session_backend_error_is_scrubbed(monkeypatch):
    """A raw backend error string is NOT passed through — clean write_failed."""
    session = _session_with_write(
        {"ok": False, "error": "OSError: [Errno 28] No space left on device: '/internal/blobs'"})
    content, art = await _call_write_workspace(
        monkeypatch, "/data/y.txt", "body", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "write_failed"
    assert content == "could not write '/data/y.txt'"
    assert "Errno" not in content                       # internal detail not leaked


@pytest.mark.asyncio
async def test_write_session_no_pool_is_no_workspace_clean(monkeypatch):
    """The internal RuntimeError (no fileop pool) becomes a CLEAN no_workspace error."""
    session = _session_with_write(raise_runtime=True)
    content, art = await _call_write_workspace(
        monkeypatch, "/data/y.txt", "body", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "no_workspace"
    assert "run_dir" not in content                     # internal not leaked
    assert "workspace" in content


# ---------------------------------------------------------------------------
# Task 5 — edit_file single-backend → Runtime-local workspace_fs.edit_file.
# The read+replace+write all happen against the sandbox; the tool maps the result.
# _edit_workspace is @tool_output decorated → returns a (content, artifact) tuple.
# ---------------------------------------------------------------------------

def _session_with_edit(result=None, raise_runtime=False):
    """A single-backend session stub whose async ``edit_file`` returns ``result``
    (an in-sandbox fileop dict), or raises an INTERNAL RuntimeError (must not leak)."""
    async def _ef(path, old, new, replace_all=False):
        if raise_runtime:
            raise RuntimeError("no sandbox for this session (run_dir is None — "
                               "InMemory / no workflow); the warm file API is unavailable")
        return result
    return types.SimpleNamespace(edit_file=_ef)


async def _call_edit_workspace(
    monkeypatch, path, old, new, session, replace_all=False
):
    """Drive the current Runtime-local edit seam without a real sandbox."""
    from vibecanvas_api.agents.tools import workspace_fs
    from vibecanvas_api.agents.tools.fs.edit_file import _edit_workspace

    monkeypatch.setattr(workspace_fs, "edit_file", session.edit_file)
    return await _edit_workspace(path, old, new, replace_all)


@pytest.mark.asyncio
async def test_edit_single_backend_unique_occurrence_succeeds(monkeypatch):
    """Flag on: the edit is applied IN the sandbox; the tool maps replacements
    through AND surfaces the sandbox's git-style diff in the agent-facing content."""
    session = _session_with_edit(
        {"ok": True, "replacements": 1, "diff": "@@ -1 +1 @@\n-a b a\n+a c a"})
    content, art = await _call_edit_workspace(
        monkeypatch, "/data/z.txt", "b", "c", session
    )
    assert art["status"] == "success"
    assert "replacement" not in content
    assert "-a b a" in content and "+a c a" in content      # the diff reaches the agent


@pytest.mark.asyncio
async def test_edit_single_backend_non_unique_without_replace_all_errors(monkeypatch):
    """Flag on: the sandbox reports not_unique → not_unique error envelope."""
    session = _session_with_edit({"ok": False, "error": "not_unique"})
    content, art = await _call_edit_workspace(
        monkeypatch, "/data/z.txt", "a", "x", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "not_unique"


@pytest.mark.asyncio
async def test_edit_single_backend_non_unique_with_replace_all_succeeds(monkeypatch):
    """Flag on: replace_all=True → the sandbox replaces every occurrence."""
    session = _session_with_edit({"ok": True, "replacements": 2})
    content, art = await _call_edit_workspace(
        monkeypatch, "/data/z.txt", "a", "x", session, replace_all=True
    )
    assert art["status"] == "success"
    assert content == "ok"
    assert art["artifact"]["handles"]["path"] == "/data/z.txt"


@pytest.mark.asyncio
async def test_edit_single_backend_missing_or_absent_string_is_not_found(monkeypatch):
    """Flag on: the sandbox folds missing-file / string-absent into not_found."""
    session = _session_with_edit({"ok": False, "error": "not_found"})
    content, art = await _call_edit_workspace(
        monkeypatch, "/data/missing.txt", "b", "c", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "not_found"
    assert "does not exist" in content                 # the message covers the missing-file case


@pytest.mark.asyncio
async def test_edit_single_backend_backend_error_is_scrubbed(monkeypatch):
    """A raw backend error string is NOT passed through — clean edit_failed."""
    session = _session_with_edit(
        {"ok": False, "error": "OSError: [Errno 13] Permission denied: '/internal/x'"})
    content, art = await _call_edit_workspace(
        monkeypatch, "/data/z.txt", "a", "x", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "edit_failed"
    assert "Errno" not in content                      # internal detail not leaked


# ---------------------------------------------------------------------------
# Task 6 — grep single-backend → Runtime-local workspace_fs.grep.
# session.grep does the recursive, binary-skipping search in the sandbox and
# returns path:line:text matches; the tool applies the optional filename glob.
# _grep_workspace is @tool_output → returns a (content, artifact) tuple.
# ---------------------------------------------------------------------------

def _session_with_grep(result=None, raise_runtime=False, captured=None):
    """A single-backend session stub whose async ``grep`` returns ``result`` (an
    in-sandbox fileop dict), or raises an INTERNAL RuntimeError (must not leak).
    Records the call args in ``captured`` when given."""
    async def _g(pattern, path, glob="", context=0):
        if captured is not None:
            captured.update(pattern=pattern, path=path, glob=glob, context=context)
        if raise_runtime:
            raise RuntimeError("no sandbox for this session (run_dir is None — "
                               "InMemory / no workflow); the warm file API is unavailable")
        return result
    return types.SimpleNamespace(grep=_g)


async def _call_grep_workspace(
    monkeypatch, pattern, prefix, glob_filter, session, context=0
):
    """Drive the current Runtime-local grep seam without a real sandbox."""
    from vibecanvas_api.agents.tools import workspace_fs
    from vibecanvas_api.agents.tools.fs.grep import _grep_workspace

    monkeypatch.setattr(workspace_fs, "grep", session.grep)
    return await _grep_workspace(pattern, prefix, glob_filter, context)


@pytest.mark.asyncio
async def test_grep_single_backend_returns_sandbox_matches(monkeypatch):
    """Flag on: matches come from the SANDBOX (session.grep), path:line:text format."""
    session = _session_with_grep(
        {"ok": True, "match_count": 2,
         "matches": ["/memory/a.md:2:needle here", "/data/b.txt:2:also needle"]})
    content, art = await _call_grep_workspace(
        monkeypatch, "needle", "/", "", session
    )
    assert art["status"] == "success"
    matches = content.splitlines()
    assert "/memory/a.md:2:needle here" in matches
    assert "/data/b.txt:2:also needle" in matches
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_grep_threads_glob_and_context_to_sandbox(monkeypatch):
    """glob + context are applied IN the sandbox (session.grep) — the tool threads
    them through (the real filtering/context is @gvisor-covered in fileops)."""
    captured = {}
    session = _session_with_grep({"ok": True, "matches": [], "match_count": 0},
                                 captured=captured)
    await _call_grep_workspace(
        monkeypatch, "needle", "/data", "*.md", session, context=2
    )
    assert captured["path"] == "/data"
    assert captured["glob"] == "*.md"
    assert captured["context"] == 2


@pytest.mark.asyncio
async def test_grep_single_backend_invalid_regex(monkeypatch):
    """Flag on: the sandbox reports invalid_regex → clean invalid_regex error."""
    session = _session_with_grep({"ok": False, "error": "invalid_regex"})
    content, art = await _call_grep_workspace(
        monkeypatch, "(unclosed", "/", "", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "invalid_regex"


@pytest.mark.asyncio
async def test_grep_single_backend_no_pool_is_no_workspace(monkeypatch):
    """The internal RuntimeError (no fileop pool) becomes a CLEAN no_workspace error."""
    session = _session_with_grep(raise_runtime=True)
    content, art = await _call_grep_workspace(
        monkeypatch, "x", "/", "", session
    )
    assert art["status"] == "error"
    assert art["error"]["code"] == "no_workspace"
    assert "run_dir" not in content


def test_ls_glob_removed():
    import vibecanvas_api.agents.tools.fs as fs
    assert not hasattr(fs, "ls") and not hasattr(fs, "glob")
    names = {t.name for t in fs.FS_TOOLS}
    assert names == {"read_file", "write_file", "edit_file", "grep"}

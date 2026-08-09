# -*- coding: utf-8 -*-
"""Task 8 — turn-end async writeback: coalesce + drain + close safety.

The methods under test live on :class:`SandboxSession`, whose real ``__init__``
needs many materialized-mount args. We construct a BARE instance (bypassing
``__init__``) and wire only the fields the writeback machinery touches, plus a
counting stand-in for the real diff-sync ``writeback_vfs`` so we can assert how
many times it ran.
"""
import asyncio

import pytest

from vibecanvas_api.services.sandbox.manager import SandboxSession


def _bare_session(delay: float = 0.0) -> SandboxSession:
    s = SandboxSession.__new__(SandboxSession)  # bypass __init__
    s._lock = asyncio.Lock()
    s._transition_lock = asyncio.Lock()
    s._lifecycle_state = "warm"
    s._lifecycle_generation = 0
    s._wb_task = None
    s._wb_pending = False
    s._inflight_operations = 0
    s.last_used = 0.0
    s.closed = False
    s.wf_id = "writeback-test"
    s.run_dir = None
    s.mount_dir = None
    s.n = 0

    async def _wb() -> None:
        if delay:
            await asyncio.sleep(delay)
        s.n += 1

    s.writeback_vfs = _wb  # override the real diff-sync with a counter
    return s


@pytest.mark.asyncio
async def test_schedule_non_blocking_then_drains():
    s = _bare_session()
    s.schedule_writeback()       # returns immediately (fire-and-forget)
    await s.drain_writeback()
    assert s.n == 1


@pytest.mark.asyncio
async def test_coalesce_single_pending():
    s = _bare_session(delay=0.05)
    s.schedule_writeback()       # starts the in-flight run
    s.schedule_writeback()       # coalesced → one pending re-run
    s.schedule_writeback()       # coalesced into the SAME single pending
    await s.drain_writeback()
    assert s.n == 2              # one in-flight + exactly one coalesced re-run


@pytest.mark.asyncio
async def test_close_awaits_inflight():
    s = _bare_session(delay=0.05)
    s.schedule_writeback()
    await s.close()              # must drain the in-flight run, not tear down mid-write
    # close() drains the scheduled run (n=1) AND does its own final writeback_vfs
    # (n=2) — see task-8-report.md "close()-count" note.
    assert s.n == 2
    assert s.closed


@pytest.mark.asyncio
async def test_schedule_after_close_is_noop():
    s = _bare_session()
    await s.close()              # final writeback → n=1, closed=True
    s.schedule_writeback()       # closed → must not start a task
    await s.drain_writeback()
    assert s.n == 1
    assert s._wb_task is None


# --- turn-boundary attach detection (agent.py AgentContext) -----------------


@pytest.mark.asyncio
async def test_context_memoizes_attached_session(monkeypatch):
    """``sandbox_session()`` resolves the manager session ONCE and memoizes it on
    ``_attached_session`` (so the turn boundary knows a session was attached and
    reuses the same instance) — the load-bearing detection for the G3 wiring."""
    from vibecanvas_api import agent as agent_mod

    sentinel = object()
    calls = {"n": 0}
    seen = {}

    class _FakeManager:
        async def get_session(self, tenant_id, wf_id, user_id=None,
                              expose_run=True):
            calls["n"] += 1
            seen.update({
                "tenant_id": tenant_id,
                "wf_id": wf_id,
                "user_id": user_id,
                "expose_run": expose_run,
            })
            return sentinel

    monkeypatch.setattr(agent_mod, "get_sandbox_manager", lambda: _FakeManager())

    ctx = agent_mod.AgentContext(tenant_id="t1", wf_id="wf1")
    assert ctx._attached_session is None        # nothing attached on a fresh ctx

    s1 = await ctx.sandbox_session()
    s2 = await ctx.sandbox_session()             # reuse, no second resolve
    assert s1 is sentinel and s2 is sentinel
    assert ctx._attached_session is sentinel
    assert calls["n"] == 1                       # resolved exactly once (memoized)
    assert seen["wf_id"] == "wf1"
    assert seen["expose_run"] is True


@pytest.mark.asyncio
async def test_context_keeps_chat_workspace_when_workflow_is_selected(monkeypatch):
    from vibecanvas_api import agent as agent_mod

    seen = {}

    class _FakeManager:
        async def get_session(self, tenant_id, wf_id, user_id=None,
                              expose_run=True):
            seen.update({
                "tenant_id": tenant_id,
                "wf_id": wf_id,
                "user_id": user_id,
                "expose_run": expose_run,
            })
            return object()

    monkeypatch.setattr(agent_mod, "get_sandbox_manager", lambda: _FakeManager())

    ctx = agent_mod.AgentContext(
        tenant_id="t1",
        wf_id="chat_ws",
        username="user1",
        current_workflow_id="wf_real",
    )
    await ctx.sandbox_session()
    assert seen == {
        "tenant_id": "t1",
        "wf_id": "chat_ws",
        "user_id": "user1",
        "expose_run": True,
    }


def test_pure_chat_turn_leaves_attached_session_none():
    """A turn that never calls sandbox_session() leaves ``_attached_session``
    None → the turn-boundary writeback (gated on it) does NOT fire / boot a
    sandbox just to write back."""
    from vibecanvas_api import agent as agent_mod

    ctx = agent_mod.AgentContext(tenant_id="t1", wf_id="wf1")
    assert getattr(ctx, "_attached_session", None) is None

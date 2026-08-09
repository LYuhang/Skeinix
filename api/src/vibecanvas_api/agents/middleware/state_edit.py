# -*- coding: utf-8 -*-
"""StateEdit — re-inject the agent-curated ``/memory/state.md`` core memory at
the absolute message tail on each model turn.

``state.md`` is the agent's self-edited, always-pinned resume anchor (goal,
current understanding, key facts/decisions, next step). It lives in the
PERSISTENT VFS (``AGENT_SANDBOX``) at ``/memory/state.md``, per-workflow, and is
maintained through normal file-editing tools. This edit re-injects it FRESH each
turn so it NEVER gets compacted away.

Mirrors ``RecitationEdit`` exactly:
- Registered AFTER ``LifecyclePolicyEdit`` (and the recitation) in
  ``_build_context_edits`` so it runs AFTER compaction — compacting the pinned
  core memory would defeat its purpose.
- Keep-latest: strips any prior state block before appending a fresh one.
- Fail-soft: missing/empty state.md (or a VFS error) → no-op, never raises.
- Byte-stable: when state.md is unchanged the appended block is byte-identical
  across turns (KV-cache discipline §4.4); the body is verbatim, not reshuffled.
- Appended as a **HumanMessage wrapped in <system-reminder>** (NOT a
  SystemMessage) — provider-agnostic tail placement (BYO-key, ONE scheme): the
  Anthropic/Gemini adapters hoist SystemMessages into their top-level system
  param, pulling the block off the tail; a human-turn message never is. Same
  rationale as ``recitation_edit.py``.

Operates on the middleware deep-copy, never the checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

STATE_HEADER = "<agent-state>"   # tag used for keep-latest stripping
STATE_PATH = "/memory/state.md"  # persistent per-workflow core-memory file


def _is_state(msg: Any) -> bool:
    return isinstance(getattr(msg, "content", None), str) and STATE_HEADER in msg.content


@dataclass
class StateEdit:
    """Pinned re-injection of ``/memory/state.md`` from the persistent VFS.

    ``vfs``: the per-turn PostgresVfsStore facade (``ctx.vfs`` — tenant injected
    from the sync tenant context var, so reads are tenant-scoped).
    ``wf_id``: the turn's workflow id (AGENT_SANDBOX is per-workflow).
    """

    vfs: Any
    wf_id: str

    def apply(self, messages: list, *, count_tokens: Any) -> None:
        try:
            # keep-latest: drop any prior state block first (so the result is
            # byte-stable when state.md is unchanged and refreshed when it isn't)
            for i in range(len(messages) - 1, -1, -1):
                if _is_state(messages[i]):
                    del messages[i]
            if not self.vfs or not self.wf_id:
                return
            entry = self.vfs.read(wf_id=self.wf_id, path=STATE_PATH)
            body = (getattr(entry, "content", None) or "") if entry is not None else ""
            if not body.strip():
                return  # absent/empty → inject nothing (cheap)
            messages.append(HumanMessage(
                content=f"<system-reminder>\n{STATE_HEADER}\n{body}\n</agent-state>\n</system-reminder>"
            ))
        except Exception:   # fail-soft: core-memory injection must never break a turn
            pass

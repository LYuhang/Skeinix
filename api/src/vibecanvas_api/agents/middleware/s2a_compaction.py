"""S2a context-aware per-output LLM compaction.

When a single tool output is OVERSIZE on first appearance, the compaction
middleware LLM-compresses it ONCE — context-aware (using the paired tool-call
args + the recent user intent) — and fills the envelope's ``llm_abstract`` slot.
The deterministic S1 ``reference`` stub then prefers ``llm_abstract`` over the
cheap ``abstract``. The original full body stays inline / in VFS (re-readable):
S2a keeps the GIST, not an elision.

Design constraints (resolved against the real wiring):

* This runs on the ``ContextEdit.apply(messages, *, count_tokens)`` seam, which
  is SYNCHRONOUS and gets NO runtime context. So the LLM call is behind an
  INJECTED ``summarize_fn(prompt) -> str`` (a thin sync adapter, built in
  ``agent._build_context_edits`` from ``agent_cfg`` where the BYO-LLM model +
  creds ARE reachable), and the persistent cache is an injected store keyed by
  the immutable ``tool_call_id``.
* Frozen-once: the (args + intent) at a tool call are immutable, so a summary is
  computed ONCE per ``tool_call_id``, persisted, and reused forever. The
  middleware runs on a per-call deep-copy, so stamping the copy does NOT persist
  across turns — the cache MUST be a real store (VFS).
* Fail-soft: a missing ``summarize_fn``/``cache``, an unreachable model, or a
  summarizer error → skip S2a and keep the deterministic ``abstract`` (S1 still
  degrades the body). Never breaks a turn.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

import structlog
from langchain_core.messages import ToolMessage

from vibecanvas_api.agents.token_accounting import count_tokens, message_tokens
from .compaction_forms import (
    output_content_type,
    output_full_tokens,
    output_path,
    parse_envelope,
)

_slog = structlog.get_logger("vibecanvas.agent.compaction.s2a")

# S2a applies only to text and log content, never tiny references.
# (link/cloud_table = a URL) or binary — those keep the deterministic abstract.
S2A_CONTENT_TYPES = frozenset({
    "text/plain", "text/markdown", "text/html", "text/shell", "application/json",
})

S2A_OVERSIZE_TOKENS_DEFAULT = 8000


# --------------------------------------------------------------------------- #
# oversize gate (pure)
# --------------------------------------------------------------------------- #

def is_oversize(tool_msg: Any, *, cap: int = S2A_OVERSIZE_TOKENS_DEFAULT,
                model: str = "") -> bool:
    """Return whether this ToolMessage output should be S2a-compressed.

    = ``meta.tokens.raw > cap`` AND the output ``content_type`` is text/log.
    Reuses ``parse_envelope`` + the recorded ``meta.tokens`` (falls back to
    counting the current content when no raw is recorded). Pure / fail-soft.
    """
    if not isinstance(tool_msg, ToolMessage):
        return False
    content = getattr(tool_msg, "content", None)
    if not isinstance(content, str):
        return False
    env = parse_envelope(content)
    if env is None:
        return False
    ct = output_content_type(env)
    if ct is None or ct.lower() not in S2A_CONTENT_TYPES:
        return False

    raw: Optional[int] = None
    tok = message_tokens(tool_msg)
    if isinstance(tok, dict) and isinstance(tok.get("raw"), int):
        raw = tok["raw"]
    if raw is None:
        # Large omitted output: the message body is tiny (data was omitted) but
        # the producer recorded the FULL size on output.full_tokens (§4.1a). Use
        # it so the gate fires on the ORIGINAL size, not the omitted body.
        full = output_full_tokens(env)
        if isinstance(full, int):
            raw = full
    if raw is None:
        raw = count_tokens(content, model)
    return raw > cap


# --------------------------------------------------------------------------- #
# context extraction + prompt builder (pure)
# --------------------------------------------------------------------------- #

def find_paired_call_args(messages: list, tool_call_id: str) -> dict:
    """The ``args`` of the AIMessage tool_call whose id == ``tool_call_id``.

    Returns ``{}`` when not found (fail-soft). The pairing is by id, not order,
    so parallel tool calls resolve correctly.
    """
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                args = tc.get("args")
                return args if isinstance(args, dict) else {}
    return {}


def latest_human_intent(messages: list) -> str:
    """The content of the most recent HumanMessage (the active task/intent).

    Empty string when none. Coerces non-str content (multimodal) to str.
    """
    for msg in reversed(messages):
        if type(msg).__name__ == "HumanMessage":
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def envelope_body(env: dict, *, vfs_reader: Any = None) -> str:
    """Return the full body to summarize for S2a.

    Preference order — read the FULL content from VFS by ``output.path`` (the
    producer wrote it there before omitting the inline ``data``; this is the bug
    fix — S2a must summarize the ORIGINAL, not the omitted message body), else
    fall back to the inline ``output.data`` (fresh-small case), else the cheap
    deterministic ``abstract``. Fail-soft: a VFS miss falls through to inline /
    abstract, never raises.
    """
    out = env.get("output") or {}
    if vfs_reader is not None:
        try:
            full = vfs_reader.read(output_path(env))
        except Exception:
            full = None
        if isinstance(full, str) and full:
            return full
    data = out.get("data") if isinstance(out, dict) else None
    if isinstance(data, str):
        return data
    if data is not None:
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return str(data)
    # No inline data + no VFS body — summarize the abstract instead.
    return str(env.get("abstract") or "")


def build_summary_prompt(tool_msg: ToolMessage, *, tool_args: dict, intent: str,
                         tool_name: str = "", vfs_reader: Any = None) -> str:
    """Build the context-aware summarizer prompt.

    Feeds the oversize body (read FULL from VFS by path when a ``vfs_reader`` is
    given — §4.1a) PLUS the paired call args (what was asked of the tool) PLUS
    the recent user intent (the active task) so the summary is targeted, not
    generic. Instructs preservation of key facts + file paths/refs.
    """
    name = tool_name or getattr(tool_msg, "name", "") or "tool"
    env = parse_envelope(getattr(tool_msg, "content", "")) or {}
    body = envelope_body(env, vfs_reader=vfs_reader)
    try:
        args_str = json.dumps(tool_args, ensure_ascii=False)
    except Exception:
        args_str = str(tool_args)
    return (
        f"Summarize this oversize `{name}` tool output with respect to the call "
        f"arguments and the current task. Preserve the key facts AND any file "
        f"paths / references / ids the agent will need to act next. Be concise — "
        f"this gist replaces the full body in the model's working context, but the "
        f"full body remains retrievable.\n\n"
        f"Call arguments: {args_str}\n"
        f"Current task / intent: {intent}\n\n"
        f"--- Tool output to summarize ---\n{body}"
    )


# --------------------------------------------------------------------------- #
# the compaction step
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class S2aCompactor:
    """Performs S2a over a message list, in place.

    ``summarize_fn``: injected ``(prompt) -> str``; the real one is a thin adapter
        over the BYO-LLM model (built in ``agent._build_context_edits``). None →
        S2a inert (fail-soft).
    ``cache``: a persistent store with ``read(tool_call_id) -> str | None`` and
        ``write(tool_call_id, text)`` (VFS-backed in prod). None → inert.
    ``cap``: oversize threshold in tokens.
    ``model``: tokenizer model for the fallback count + meta.tokens.compressed.
    ``vfs_reader``: reads the FULL body back from VFS by ``output.path`` (§4.1a)
        so the gist summarizes the ORIGINAL, not the omitted message body. None →
        fall back to inline data / abstract (fail-soft).
    """
    summarize_fn: Optional[Callable[[str], str]] = None
    cache: Any = None
    cap: int = S2A_OVERSIZE_TOKENS_DEFAULT
    model: str = ""
    vfs_reader: Any = None

    def apply(self, messages: list) -> None:
        if self.summarize_fn is None or self.cache is None:
            return  # not wired in this context → fall back to S1 deterministic abstract
        for idx, msg in enumerate(messages):
            try:
                if not is_oversize(msg, cap=self.cap, model=self.model):
                    continue
                tcid = getattr(msg, "tool_call_id", None)
                if not tcid:
                    continue
                env = parse_envelope(msg.content)
                if env is None or env.get("llm_abstract"):
                    continue  # frozen-once: already filled
                gist = self._gist(messages, idx, msg, tcid)
                if not gist:
                    continue
                messages[idx] = self._fill(msg, env, gist)
            except Exception:
                # Fail-soft per message: S2a error → keep deterministic abstract.
                continue

    def _gist(self, messages: list, idx: int, msg: ToolMessage, tcid: str) -> str:
        """Read-before-compute, write-after. Returns the gist (cached or fresh)."""
        cached = self.cache.read(tcid)
        if isinstance(cached, str) and cached:
            return cached
        args = find_paired_call_args(messages, tcid)
        intent = latest_human_intent(messages[:idx])
        prompt = build_summary_prompt(
            msg, tool_args=args, intent=intent, tool_name=getattr(msg, "name", ""),
            vfs_reader=self.vfs_reader)
        gist = self.summarize_fn(prompt)
        if not isinstance(gist, str) or not gist.strip():
            return ""
        self.cache.write(tcid, gist)
        try:
            _slog.info("s2a_compaction", tool_call_id=tcid, tool=getattr(msg, "name", ""),
                       gist_tokens=count_tokens(gist, self.model))
        except Exception:
            pass
        return gist

    @staticmethod
    def cache_path(tool_call_id: str) -> str:
        """The VFS artifact path the S2a gist for ``tool_call_id`` is cached at.

        Lives under ``/exec/__compaction__/`` — the run-execution tier, hidden by
        the double-underscore convention (not user-facing scratch). Keyed by the
        immutable tool_call_id so the gist is byte-stable + reusable forever.
        """
        return f"/exec/__compaction__/{tool_call_id}.txt"

    def _fill(self, msg: ToolMessage, env: dict, gist: str) -> ToolMessage:
        """Set ``llm_abstract`` on the envelope + record meta.tokens.compressed.

        The body (output.data) is LEFT INTACT — S2a is a gist, not an elision;
        the deterministic ``abstract`` is also kept (S1 reference now prefers
        llm_abstract). Returns a new ToolMessage (consistent with S1's _stamp).
        """
        new_env = {**env, "llm_abstract": gist}
        new_content = json.dumps(new_env, ensure_ascii=False)
        new_msg = msg.model_copy(update={"content": new_content})

        # Record meta.tokens.compressed on the copy (the running-size estimate
        # reads it once S1 degrades this message to a compressed/reference form).
        try:
            tok = message_tokens(new_msg)
            if not isinstance(tok, dict):
                tok = {"raw": None, "abstract": None, "form": "raw", "model": self.model}
                new_msg.response_metadata = {**getattr(new_msg, "response_metadata", {}),
                                             "tokens": tok}
            tok["compressed"] = count_tokens(gist, self.model)
        except Exception:
            pass
        return new_msg


# --------------------------------------------------------------------------- #
# VFS-backed persistent cache (production)
# --------------------------------------------------------------------------- #

class VfsS2aCache:
    """Persistent S2a gist cache over the wired ``PostgresVfsStore``.

    The compaction middleware runs on a per-call deep-copy, so stamping the copy
    does NOT persist a gist across turns — this cache is the real store. Keyed by
    the immutable ``tool_call_id`` (→ a tiny ``/exec/__compaction__/<tcid>.txt``
    artifact). Sync (matches the synchronous ``ContextEdit.apply`` seam — the VFS
    store internally bridges to its async repo via ``run_in_short_session``).
    Fully fail-soft: any store error → treated as a miss / no-op so S2a falls back
    to recompute or, worst case, the deterministic abstract.
    """

    def __init__(self, vfs_store: Any, wf_id: str):
        self._vfs = vfs_store
        self._wf_id = wf_id

    def read(self, tool_call_id: str) -> Optional[str]:
        try:
            entry = self._vfs.read(wf_id=self._wf_id,
                                   path=S2aCompactor.cache_path(tool_call_id))
        except Exception:
            return None
        if entry is None:
            return None
        # VfsEntry carries the body on ``.content`` (text) — be tolerant of shape.
        content = getattr(entry, "content", None)
        if isinstance(content, str) and content:
            return content
        if isinstance(entry, str):
            return entry or None
        return None

    def write(self, tool_call_id: str, text: str) -> None:
        try:
            self._vfs.upsert_artifact(
                wf_id=self._wf_id,
                path=S2aCompactor.cache_path(tool_call_id),
                content=text,
                content_type="text/plain",
                abstract="S2a compaction gist",
            )
        except Exception:
            return


class VfsBodyReader:
    """Read the full body of a tool output back from VFS by ``path``.

    The compaction middleware re-hydrates a large output's in-context FORM
    (head+tail tier-2 / S2a gist tier-4) from the FULL body — the original
    producer wrote it to VFS at ``output.path`` before omitting the inline data.
    Sync (matches the synchronous ``ContextEdit.apply`` seam; the PostgresVfsStore
    bridges to its async repo internally). Fully fail-soft: any store error / a
    missing path / a binary entry → None so the caller falls back to the cheap
    abstract+path stub (never breaks a turn)."""

    def __init__(self, vfs_store: Any, wf_id: str):
        self._vfs = vfs_store
        self._wf_id = wf_id

    def read(self, path: Optional[str]) -> Optional[str]:
        if not path or not isinstance(path, str):
            return None
        try:
            entry = self._vfs.read(wf_id=self._wf_id, path=path)
        except Exception:
            return None
        if entry is None:
            return None
        content = getattr(entry, "content", None)
        if isinstance(content, str) and content:
            return content
        if isinstance(entry, str):
            return entry or None
        return None

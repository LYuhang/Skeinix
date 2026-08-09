"""S2b whole-prefix LLM summary with a non-destructive VFS range cache.

The range cache and token metadata make summaries stable across resumed turns.

THE LONG-SESSION SAFETY NET. When the running context size crosses a TRIGGER,
LLM-summarize the OLDEST contiguous chunk of the conversation into ONE compact
summary message that REPLACES that range in the per-call view (the
ContextEditingMiddleware deep-copy — the checkpointer is NEVER mutated). The raw
turns stay in the checkpointer (non-destructive); the summary is cached by its
immutable message-id range so a resume never re-LLMs (frozen-once by range).

Design constraints (resolved against the real wiring — mirrors S2a):

* This runs on the synchronous ``ContextEdit.apply(messages, *, count_tokens)``
  seam (inside ``LifecyclePolicyEdit``), which gets NO runtime context. So the
  LLM call is behind an INJECTED ``summarize_fn(prompt) -> str`` (built in
  ``agent._build_s2b_summarize_fn`` from ``agent_cfg`` where the BYO-LLM model +
  creds ARE reachable) and the persistent cache is an injected store keyed by the
  immutable range ``(thread_id, last_message_id, summarizer_version)``.
* Hysteresis: act ONLY when ``estimate_context_tokens > trigger``, then summarize
  the oldest prefix in ONE shot until the projected estimate would drop below
  ``target`` — never act between target and trigger (no per-turn thrash).
* Frozen-once by range: raw is append-only, so ``[0, end]`` is content-stable
  forever → the cached summary for a range never invalidates. Incremental: a new
  chunk ``[a, b]`` past a cached ``[0, a]`` is summarized ALONE and CHAINED onto
  the prior summary (never re-summarize an already-cached prefix).
* Fail-soft: a missing ``summarize_fn``/``cache``, an unreachable model, a
  summarizer error, or any VFS error → S2b returns None (no change) and the turn
  falls back to S1 only. Never breaks a turn.

Composition order with S1/S2a: S2b runs first among
the compaction stages (after the superseded-projection sweep, before S2a /
head+tail / S1). It collapses the OLD prefix into one summary so the downstream
passes only operate on the still-shown middle + live tail. This avoids
double-work (S1 would otherwise recency-degrade the same old messages S2b is
about to subsume) and keeps the summary coherent over the original turns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import structlog
from langchain_core.messages import SystemMessage

from vibecanvas_api.agents.token_accounting import (
    count_tokens,
    estimate_context_tokens,
)

_slog = structlog.get_logger("vibecanvas.agent.compaction.s2b")

# Bump this when the summarizer PROMPT or behaviour changes — it is part of the
# range cache key so a new prompt forces a recompute (harness §3.2). The MODEL is
# resolved per-run (resolve_compaction_model) and is NOT in the key: a model swap
# does not invalidate a human-readable prefix summary (cheap-utility-model intent).
S2B_SUMMARIZER_VERSION = "v1"

# Defaults are overridable through ``agent.compaction.*`` configuration.
S2B_TRIGGER_TOKENS_DEFAULT = 120_000
S2B_TARGET_TOKENS_DEFAULT = 60_000
# The pinned head is never summarized: the system message and first human
# task. The live tail is the recent window S1 also keeps full.
S2B_PINNED_HEAD_DEFAULT = 2
S2B_LIVE_TAIL_DEFAULT = 4


# --------------------------------------------------------------------------- #
# value object: a cached segment summary covering an immutable id range
# --------------------------------------------------------------------------- #

@dataclass(slots=True, frozen=True)
class S2bSummary:
    """One cached segment summary covering messages ``[start_idx, end_idx]`` whose
    last covered message has id ``last_id``. ``text`` is the LLM summary. The
    chain of these (oldest→newest) is what gets concatenated into the single
    in-context summary message."""
    last_id: str
    text: str


# --------------------------------------------------------------------------- #
# stable message identity
# --------------------------------------------------------------------------- #

def _msg_id(msg: Any, idx: int) -> str:
    """A stable id for a message. LangChain assigns ``.id`` once persisted; for a
    not-yet-persisted message (id None) fall back to a positional id so the range
    key is still deterministic within the call (the cache is best-effort then)."""
    mid = getattr(msg, "id", None)
    if isinstance(mid, str) and mid:
        return mid
    return f"_pos_{idx}"


# --------------------------------------------------------------------------- #
# range selection (pure)
# --------------------------------------------------------------------------- #

def select_summary_range(
    messages: list,
    *,
    estimate: Callable[[list], int],
    model: str,
    target: int,
    live_tail: int,
    pinned_head: int,
) -> Optional[tuple[int, int]]:
    """Choose the contiguous OLD prefix ``[start, end]`` (inclusive) to summarize.

    KEEPS the pinned head (``messages[:pinned_head]`` — system + first human) and
    the recent live tail (the last ``live_tail`` messages, never summarized).
    Extends ``end`` from the oldest summarizable message forward until replacing
    ``[start, end]`` with a (≈0-token) summary would bring the projected estimate
    below ``target``. Returns None when there is nothing summarizable (the prefix
    between the pinned head and the live tail is empty) — pure / fail-soft.
    """
    n = len(messages)
    start = max(0, pinned_head)
    last_summarizable = n - live_tail - 1  # inclusive upper bound for ``end``
    if last_summarizable < start:
        return None  # head + tail cover everything; no prefix to collapse

    # Walk end forward; stop as soon as removing [start, end] drops us < target.
    for end in range(start, last_summarizable + 1):
        remaining = messages[:start] + messages[end + 1:]
        if estimate(remaining) < target:
            return (start, end)
    # Couldn't get below target even summarizing the whole eligible prefix —
    # still summarize the maximal prefix (best effort).
    return (start, last_summarizable)


# --------------------------------------------------------------------------- #
# prompt builder (context-aware; harness §3.5)
# --------------------------------------------------------------------------- #

def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    return "" if content is None else str(content)


def build_summary_prompt(
    range_messages: list,
    *,
    prior_summary: str = "",
) -> str:
    """Build the whole-prefix summarizer prompt (harness §3.5).

    Instructs preservation of refs/file paths, goals/progress/remaining work, and
    critical decisions; human-readable & inspectable. ``prior_summary`` (the
    chained earlier segments) is handed in as the running context so an
    INCREMENTAL chunk summarizes only the NEW turns while staying coherent with
    what came before — the model never re-reads the already-summarized prefix.
    """
    transcript = []
    for m in range_messages:
        role = type(m).__name__.replace("Message", "")
        transcript.append(f"[{role}] {_message_text(m)}")
    body = "\n".join(transcript)
    prior_block = (
        f"Summary of the conversation BEFORE this chunk (build on it, do not "
        f"repeat it verbatim):\n{prior_summary}\n\n" if prior_summary else "")
    return (
        "You are compacting an older slice of an ongoing agent conversation to "
        "save context. Write a concise, human-readable summary of the slice "
        "below. You MUST preserve: the user's goals, progress so far and "
        "remaining work; critical decisions; and ALL file paths / references / "
        "ids the agent will need to continue (these are how artifacts in the "
        "compacted range stay reachable). Do not invent facts.\n\n"
        f"{prior_block}"
        f"--- Conversation slice to summarize ---\n{body}"
    )


# --------------------------------------------------------------------------- #
# the compactor
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class S2bCompactor:
    """Collapse the oldest prefix into one cached summary message on the deep-copy.

    ``summarize_fn``: injected ``(prompt) -> str``; real one is a thin sync adapter
        over the BYO-LLM model (built in ``agent._build_s2b_summarize_fn``). None →
        inert (fail-soft → S1 only).
    ``cache``: persistent store with ``read(range_key) -> str | None`` and
        ``write(range_key, text)`` (VFS-backed in prod). None → inert.
    ``thread_id``: the conversation/workflow id — first component of the range key.
    ``model``: tokenizer model for the running-size estimate + compressed count.
    ``trigger`` / ``target``: hysteresis band (act only when estimate > trigger;
        summarize until projected estimate < target).
    ``pinned_head`` / ``live_tail``: the never-summarized head + recent tail.
    """
    summarize_fn: Optional[Callable[[str], str]] = None
    cache: Any = None
    thread_id: str = ""
    model: str = ""
    trigger: int = S2B_TRIGGER_TOKENS_DEFAULT
    target: int = S2B_TARGET_TOKENS_DEFAULT
    pinned_head: int = S2B_PINNED_HEAD_DEFAULT
    live_tail: int = S2B_LIVE_TAIL_DEFAULT

    def apply(self, messages: list) -> Optional[list]:
        """Return a NEW message list with the old prefix replaced by one summary
        message, or None when S2b does not act (below trigger / inert / fail-soft).

        Does NOT mutate ``messages`` (the input is the shared deep-copy that the
        rest of the compaction chain also uses — the caller swaps in the returned
        view). The raw turns are preserved in the checkpointer regardless.
        """
        if self.summarize_fn is None or self.cache is None:
            return None
        try:
            est = estimate_context_tokens(messages, model=self.model)
            if est <= self.trigger:
                return None  # hysteresis: do nothing between target and trigger
            try:
                _slog.warning("s2b_compaction_triggered", thread_id=self.thread_id,
                              tokens_estimate=est, trigger=self.trigger,
                              target=self.target, model=self.model,
                              message_count=len(messages))
            except Exception:
                pass
            rng = select_summary_range(
                messages, estimate=lambda m: estimate_context_tokens(m, model=self.model),
                model=self.model, target=self.target,
                live_tail=self.live_tail, pinned_head=self.pinned_head)
            if rng is None:
                try:
                    _slog.warning("s2b_compaction_no_range", thread_id=self.thread_id,
                                  tokens_estimate=est, trigger=self.trigger,
                                  target=self.target, model=self.model,
                                  message_count=len(messages),
                                  pinned_head=self.pinned_head,
                                  live_tail=self.live_tail)
                except Exception:
                    pass
                return None
            start, end = rng
            chain = self._segment_chain(messages, start, end)
            if not chain:
                return None
            summary_text = "\n\n".join(f"[segment {i + 1}] {seg.text}"
                                       for i, seg in enumerate(chain))
            summ_msg = self._build_summary_message(summary_text, chain[-1].last_id)
            view = messages[:start] + [summ_msg] + messages[end + 1:]
            try:
                _slog.warning("s2b_compaction_done", thread_id=self.thread_id,
                              range_start=start, range_end=end, segments=len(chain),
                              tokens_before=est,
                              tokens_after=estimate_context_tokens(view, model=self.model),
                              trigger=self.trigger, target=self.target,
                              model=self.model)
            except Exception:
                pass
            return view
        except Exception:
            # Fail-soft: any S2b error → no change, fall back to S1 only.
            return None

    # ----------------------------------------------------------------- #
    # incremental segment chain
    # ----------------------------------------------------------------- #

    def _segment_chain(self, messages: list, start: int, end: int) -> list[S2bSummary]:
        """Build the ordered chain of cached segment summaries covering
        ``[start, end]``. Reuses cached segments for any already-summarized
        sub-prefix and computes (then caches) ONLY the trailing NEW chunk —
        chaining it onto the prior summary so the incremental segment stays
        coherent without re-summarizing the cached prefix."""
        # Walk the covered messages; greedily reuse the LONGEST cached prefix
        # segment whose last id is a covered message, then summarize the rest.
        chain: list[S2bSummary] = []
        cursor = start  # first not-yet-covered index within [start, end]

        # Reuse: find cached segments by scanning covered ids for a cache hit.
        # We keep it simple + deterministic: try every covered boundary id from
        # the LAST toward the cursor; the farthest cached boundary wins as the
        # reused prefix (one segment), and the remainder is the new chunk.
        reused_until = start - 1  # exclusive: messages[start..reused_until] cached
        reused_text = ""
        for k in range(end, start - 1, -1):
            key = self._range_key(self._covered_last_id(messages, k))
            cached = self._cache_read(key)
            if cached is not None:
                reused_until = k
                reused_text = cached
                break

        if reused_until >= start:
            chain.append(S2bSummary(last_id=self._covered_last_id(messages, reused_until),
                                    text=reused_text))
            cursor = reused_until + 1

        if cursor <= end:
            new_chunk = messages[cursor:end + 1]
            prompt = build_summary_prompt(new_chunk, prior_summary=reused_text)
            text = self.summarize_fn(prompt)
            if not isinstance(text, str) or not text.strip():
                return chain  # summarizer produced nothing → keep what we reused
            last_id = self._covered_last_id(messages, end)
            self._cache_write(self._range_key(last_id), text)
            chain.append(S2bSummary(last_id=last_id, text=text))
        return chain

    @staticmethod
    def _covered_last_id(messages: list, idx: int) -> str:
        return _msg_id(messages[idx], idx)

    def _range_key(self, last_id: str) -> str:
        """Immutable range cache key: ``(thread_id, last_message_id,
        summarizer_version)``. ``[0, last_id]`` is content-stable because raw is
        append-only → never invalidates; a prompt change bumps the version."""
        return f"{self.thread_id}:{last_id}:{S2B_SUMMARIZER_VERSION}"

    # ----------------------------------------------------------------- #
    # cache (fail-soft wrappers)
    # ----------------------------------------------------------------- #

    def _cache_read(self, key: str) -> Optional[str]:
        try:
            v = self.cache.read(key)
            return v if isinstance(v, str) and v else None
        except Exception:
            return None

    def _cache_write(self, key: str, text: str) -> None:
        try:
            self.cache.write(key, text)
        except Exception:
            return

    # ----------------------------------------------------------------- #
    # the summary message
    # ----------------------------------------------------------------- #

    def _build_summary_message(self, summary_text: str, last_id: str) -> SystemMessage:
        """The single in-context summary message (a marked ``SystemMessage``).

        Carries the chained segment summaries. Stamped ``response_metadata.s2b``
        (frozen-once marker + the covered range's last id) and a ``meta.tokens``
        record with ``form='compressed'`` so ``estimate_context_tokens`` counts it
        at its compressed size (the running-size sum after S2b). Stable id derived
        from the range so the prefix stays byte-identical across turns (KV-cache
        discipline, harness §3.4)."""
        content = (
            "<conversation summary — older turns compacted to save context; the "
            "full raw history is retained and the referenced files/ids below "
            "remain reachable>\n" + summary_text)
        compressed = count_tokens(content, self.model)
        msg = SystemMessage(
            content=content,
            id=f"s2b_summary_{self.thread_id}_{last_id}",
            response_metadata={
                "s2b": {"covered_last_id": last_id, "version": S2B_SUMMARIZER_VERSION},
                "tokens": {"raw": compressed, "abstract": None,
                           "compressed": compressed, "form": "compressed",
                           "model": self.model},
            },
        )
        return msg


# --------------------------------------------------------------------------- #
# VFS-backed persistent range cache (production) — mirrors VfsS2aCache
# --------------------------------------------------------------------------- #

class VfsS2bCache:
    """Persistent S2b segment-summary cache over the wired ``PostgresVfsStore``.

    Keyed by the immutable range key ``(thread_id, last_message_id, version)`` →
    a tiny ``/exec/__compaction__/summary_<last_id>.txt`` artifact (the hidden
    run-execution compaction tier, same convention as S2a). Sync (matches the
    synchronous ``ContextEdit.apply`` seam; the store bridges to its async repo
    internally). Fully fail-soft: any store error → a miss / no-op so S2b
    recomputes or, worst case, falls back to S1.
    """

    def __init__(self, vfs_store: Any, wf_id: str):
        self._vfs = vfs_store
        self._wf_id = wf_id

    @staticmethod
    def cache_path(range_key: str) -> str:
        """VFS artifact path for a range key. Uses the last_message_id component
        (the stable, filesystem-safe middle of the key) for a readable filename."""
        parts = range_key.split(":")
        last_id = parts[1] if len(parts) >= 2 else range_key
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in last_id)
        return f"/exec/__compaction__/summary_{safe}.txt"

    def read(self, range_key: str) -> Optional[str]:
        try:
            entry = self._vfs.read(wf_id=self._wf_id, path=self.cache_path(range_key))
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

    def write(self, range_key: str, text: str) -> None:
        try:
            self._vfs.upsert_artifact(
                wf_id=self._wf_id,
                path=self.cache_path(range_key),
                content=text,
                content_type="text/plain",
                abstract="S2b prefix-summary segment",
            )
        except Exception:
            return

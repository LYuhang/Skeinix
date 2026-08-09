"""LifecyclePolicyEdit — policy-driven, cache-stable tool-output compaction.

Runs on the ContextEditingMiddleware per-call deep copy (never the checkpoint).
Two main passes:
  1. semantic keep-latest sweep for volatile/snapshot tools.
  2. token-gated compaction — degrade older standard tool outputs oldest-first
     to their content_type policy's form, protecting each type's fresh_k.
Stamps response_metadata.context_editing.cleared for in-call idempotence; the
output is a deterministic function of the immutable history → cache-stable
across turns.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.token_accounting import stamp_form
from vibecanvas_api.agents.token_accounting import message_tokens
from .compaction_policy import policy_for
from .compaction_forms import (
    output_content_type,
    output_full_tokens,
    output_path,
    parse_envelope,
    render_aged,
    render_head_tail_input,
    render_head_tail_notice,
)

# Text and log bodies can be meaningfully truncated to head and tail. Tiny
# references and binary values keep only their abstract and path.
_HEADTAIL_CONTENT_TYPES = frozenset({
    "text/plain", "text/markdown", "text/html", "text/shell", "application/json",
})

_slog = structlog.get_logger("vibecanvas.agent.compaction")

_KEEP_LATEST_TOOLS = frozenset({
    "get_config",
    "list_workflows",
    "set_workflow",
    "create_workflow",
    "get_workflow",
    "check_workflow",
    "update_canvas",
    "node_execute",
    "run_workflow",
    "batch_execute",
    "get_node_spec",
})

_FILE_CONTEXT_TOOLS = frozenset({"read_file", "write_file", "edit_file"})
_FILE_MUTATION_TOOLS = frozenset({"write_file"})
_WRITE_EDIT_STRING_ARGS = {
    "write_file": ("content",),
}
_DEFAULT_FILE_CONTEXT_TIERS = (
    {"max_tokens": 2000, "full_rounds": None},
    {"max_tokens": 16000, "full_rounds": 16},
    {"max_tokens": 32000, "full_rounds": 8},
    {"max_tokens": 64000, "full_rounds": 1},
    {"max_tokens": None, "full_rounds": 0},
)


def _cleared(msg: Any) -> bool:
    return bool(getattr(msg, "response_metadata", {}).get("context_editing", {}).get("cleared"))


def _tool_name(msg: Any) -> str | None:
    name = getattr(msg, "name", None)
    if isinstance(name, str) and name:
        return name
    artifact = getattr(msg, "artifact", None)
    if isinstance(artifact, dict):
        meta = artifact.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("tool"), str):
            return meta["tool"]
    return None


def _json_obj(text: Any) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _artifact_handles(msg: Any) -> dict:
    artifact = getattr(msg, "artifact", None)
    if not isinstance(artifact, dict):
        return {}
    body = artifact.get("artifact")
    if not isinstance(body, dict):
        return {}
    handles = body.get("handles")
    return handles if isinstance(handles, dict) else {}


def _artifact_payload(msg: Any) -> dict:
    artifact = getattr(msg, "artifact", None)
    payload = artifact.get("payload") if isinstance(artifact, dict) else None
    return payload if isinstance(payload, dict) else {}


def _artifact_meta(msg: Any) -> dict:
    artifact = getattr(msg, "artifact", None)
    meta = artifact.get("meta") if isinstance(artifact, dict) else None
    return meta if isinstance(meta, dict) else {}


def _artifact_status(msg: Any) -> str | None:
    artifact = getattr(msg, "artifact", None)
    if isinstance(artifact, dict) and isinstance(artifact.get("status"), str):
        return artifact["status"]
    obj = _json_obj(getattr(msg, "content", None))
    status = obj.get("status") if isinstance(obj, dict) else None
    return status if isinstance(status, str) else None


def _tool_path(msg: Any) -> str | None:
    handles = _artifact_handles(msg)
    for key in ("path", "ref"):
        val = handles.get(key)
        if isinstance(val, str) and val:
            return val
    artifact = getattr(msg, "artifact", None)
    if isinstance(artifact, dict):
        body = artifact.get("artifact")
        if isinstance(body, dict):
            target = body.get("target")
            if isinstance(target, dict) and isinstance(target.get("path"), str):
                return target["path"]
        payload = artifact.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("ref"), str):
            return payload["ref"]
    env = parse_envelope(getattr(msg, "content", None))
    path = output_path(env) if env else None
    return path if isinstance(path, str) and path else None


def _file_content_type(msg: Any) -> str | None:
    meta = _artifact_meta(msg)
    ct = meta.get("content_type")
    if isinstance(ct, str) and ct:
        return ct
    env = parse_envelope(getattr(msg, "content", None))
    return output_content_type(env) if env else None


def _keep_latest_key(msg: ToolMessage) -> tuple | None:
    name = _tool_name(msg)
    if name not in _KEEP_LATEST_TOOLS:
        return None

    handles = _artifact_handles(msg)
    content_obj = _json_obj(getattr(msg, "content", None)) or {}

    if name == "get_config":
        return (name, handles.get("scope") or content_obj.get("scope") or "*")
    if name == "get_node_spec":
        return (name, handles.get("node_type") or content_obj.get("node_type") or "*")
    if name == "check_workflow":
        return (name, handles.get("path") or content_obj.get("path") or "*")
    if name in {"get_workflow", "update_canvas"}:
        return (name, handles.get("workflow_id") or content_obj.get("workflow_id") or "*",
                handles.get("path") or content_obj.get("path") or "")
    if name == "node_execute":
        return (name, handles.get("workflow_id") or content_obj.get("workflow_id") or "*",
                handles.get("node_id") or content_obj.get("node_id") or "*")
    if name == "run_workflow":
        return (name, handles.get("workflow_id") or content_obj.get("workflow_id") or "*")
    if name == "batch_execute":
        return (name, handles.get("workflow_id") or content_obj.get("workflow_id") or "*")
    if name == "list_workflows":
        return (name,)
    if name in {"set_workflow", "create_workflow"}:
        return ("current_workflow_context",)
    return (name,)


def _stamp(msg: ToolMessage, content: str, strategy: str, form: str | None = None,
          cleared: bool = True) -> ToolMessage:
    """Stamp a degraded form on a copy. ``cleared=True`` (S1 / superseded) marks
    the message terminal for this pass — ``_compact`` skips it. Tier-2 head+tail
    passes ``cleared=False`` so it is NOT terminal: S1 can still degrade it further
    once it ages out of the fresh window (tier-2 → tier-3). ``strategy`` keeps the
    pass idempotent (re-running skips a message it already produced)."""
    ce: dict[str, Any] = {"cleared": cleared, "strategy": strategy}
    if form:
        ce["form"] = form
    return msg.model_copy(update={
        "content": content,
        "response_metadata": {**getattr(msg, "response_metadata", {}), "context_editing": ce},
    })


def _current_form(msg: Any) -> str:
    """Return the current effective form of a message.

    A degraded ToolMessage carries the form in its ``context_editing`` stamp; the
    superseded-projection sweep maps to 'reference'. Everything else is 'raw'.
    """
    rm = getattr(msg, "response_metadata", {}) or {}
    # The S2b prefix summary is recorded 'compressed' at creation (its own tokens
    # dict) — keep it so _record_tokens doesn't flip it back to 'raw'.
    if rm.get("s2b"):
        return "compressed"
    ce = rm.get("context_editing", {}) or {}
    form = ce.get("form")
    if form:
        return form
    return "raw"


def _form_token_field(form: str) -> str:
    if form == "head_tail":
        return "head_tail"
    if form in {"abstract", "reference", "minimal"}:
        return "abstract"
    if form == "compressed":
        return "compressed"
    return "raw"


@dataclass(slots=True)
class LifecyclePolicyEdit:
    trigger: int = 80_000
    clear_at_least: int = 20_000
    model: str = ""   # active agent model — tokenizer for meta.tokens (§4.6)
    # S2a is optional context-aware per-output LLM compaction. When a
    # ``S2aCompactor`` is injected (wired in agent._build_context_edits where the
    # BYO-LLM model + a VFS cache are reachable), oversize outputs get an
    # ``llm_abstract`` BEFORE the S1 degrade below — so a later reference stub
    # prefers it. None → pure S1 (fail-soft).
    s2a: Any = None
    # S2b is the whole-prefix LLM summary used as a long-session safety net.
    # When ``estimate_context_tokens`` crosses a TRIGGER, an injected
    # ``S2bCompactor`` collapses the OLDEST prefix (keeping the pinned head + a
    # recent live tail) into ONE cached summary message — on this deep-copy ONLY
    # (the checkpointer keeps the raw). Runs FIRST among the compaction stages
    # (after the superseded sweep, before S2a / head+tail / S1) so the downstream
    # passes operate on the smaller still-shown region. None → no S2b (pure S1).
    s2b: Any = None
    # Tier-2 head/tail is the default in-context form for a fresh
    # LARGE tool output: read the FULL body from VFS by ``output.path`` and render
    # head+tail+notice (deterministic, no LLM). ``vfs_reader`` reads the full body
    # back (a ``VfsBodyReader``); None → tier-2 inert (the omitted output keeps its
    # abstract+path envelope, fail-soft). ``headtail_threshold`` reuses the S2a
    # oversize gate (8k); head/tail token budgets default 1500/500.
    vfs_reader: Any = None
    headtail_head_tokens: int = 1500
    headtail_tail_tokens: int = 500
    headtail_threshold: int = 8000
    file_context_tiers: list | tuple | None = None
    file_context_head_tokens: int = 2000
    file_context_tail_tokens: int = 2000
    file_input_head_tokens: int = 512
    file_input_tail_tokens: int = 512
    max_node_specs: int = 5
    form_projection_holder: dict | None = None
    # Unlike content-type ``fresh_k`` (number of recent outputs), interactive
    # cards are protected by Human/Chat turns. This keeps every card created in
    # the latest K user turns intact, even when a turn emits several cards.
    interactive_artifact_protect_recent_rounds: int = 3

    def apply(self, messages: list, *, count_tokens: Any) -> None:
        self._sweep_keep_latest_tools(messages)
        rounds, current_round = self._message_rounds(messages)
        self._compact_successful_file_inputs(messages)
        self._apply_file_output_policy(messages, rounds, current_round, count_tokens)
        self._run_s2b(messages)
        self._run_s2a(messages)
        self._run_head_tail(messages)
        self._compact(messages, count_tokens, rounds, current_round)
        self._record_tokens(messages)

    def _message_rounds(self, messages: list) -> tuple[dict[int, int], int]:
        rounds: dict[int, int] = {}
        current_round = 0
        seen_human = False
        for idx, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                if seen_human:
                    current_round += 1
                else:
                    seen_human = True
            rounds[idx] = current_round
        return rounds, current_round

    def _file_tier(self, tokens: int) -> dict:
        tiers = self.file_context_tiers or _DEFAULT_FILE_CONTEXT_TIERS
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            mx = tier.get("max_tokens")
            if mx is None or tokens <= int(mx):
                return tier
        return _DEFAULT_FILE_CONTEXT_TIERS[-1]

    def _run_s2b(self, messages: list) -> None:
        """Run S2b first among the compaction stages.

        Above the trigger, collapse the oldest prefix into one
        cached summary message so S2a / head+tail / S1 only see the smaller
        still-shown region (avoids double-work — S1 would otherwise recency-degrade
        the same old messages S2b subsumes). ``S2bCompactor.apply`` returns a NEW
        view (or None when it does not act); we splice it in place because the
        ContextEditingMiddleware reads back the SAME list object (its return value
        is ignored), and S2b changes the list LENGTH. Fail-soft: any error → leave
        the list as-is (fall back to S1)."""
        if self.s2b is None:
            return
        try:
            view = self.s2b.apply(messages)
            if view is not None:
                messages[:] = view
        except Exception:
            pass

    def _compact_successful_file_inputs(self, messages: list) -> None:
        """Shrink large write/edit input strings only after the matching tool call
        succeeded. Failed writes/edits keep the exact original arguments so the
        agent can retry without losing data."""
        results: dict[str, tuple[str | None, str | None]] = {}
        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            call_id = getattr(msg, "tool_call_id", None)
            if isinstance(call_id, str) and call_id:
                results[call_id] = (_tool_name(msg), _artifact_status(msg))

        for idx, msg in enumerate(messages):
            if not isinstance(msg, AIMessage):
                continue
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                continue
            changed = False
            new_calls: list[dict] = []
            for call in calls:
                if not isinstance(call, dict):
                    new_calls.append(call)
                    continue
                name = call.get("name")
                if name not in _FILE_MUTATION_TOOLS:
                    new_calls.append(call)
                    continue
                call_id = call.get("id")
                result_name, status = results.get(call_id, (None, None))
                if result_name not in (None, name) or status != "success":
                    new_calls.append(call)
                    continue
                args = call.get("args")
                if not isinstance(args, dict):
                    new_calls.append(call)
                    continue
                next_args = dict(args)
                for key in _WRITE_EDIT_STRING_ARGS.get(name, ()):
                    value = next_args.get(key)
                    if isinstance(value, str):
                        next_args[key] = self._abbreviate_file_input(value, tool=name, arg=key)
                if next_args != args:
                    call = {**call, "args": next_args}
                    changed = True
                new_calls.append(call)
            if changed:
                messages[idx] = msg.model_copy(update={"tool_calls": new_calls})

    def _abbreviate_file_input(self, text: str, *, tool: str, arg: str) -> str:
        approx_tokens = max(1, len(text) // 4)
        budget = max(1, self.file_input_head_tokens + self.file_input_tail_tokens)
        if approx_tokens <= budget:
            return text
        rendered = render_head_tail_input(
            text, head_tokens=self.file_input_head_tokens,
            tail_tokens=self.file_input_tail_tokens, model=self.model,
            full_tokens=approx_tokens,
        )
        return rendered

    def _apply_file_output_policy(
        self,
        messages: list,
        rounds: dict[int, int],
        current_round: int,
        count_tokens: Any,
    ) -> None:
        for idx, msg in enumerate(messages):
            if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
                continue
            name = _tool_name(msg)
            if name not in _FILE_CONTEXT_TOOLS:
                continue
            if _artifact_status(msg) != "success":
                continue
            full_tokens = count_tokens([msg])
            tier = self._file_tier(full_tokens)
            full_rounds = tier.get("full_rounds")
            if full_rounds is None:
                continue
            age = current_round - rounds.get(idx, current_round)
            if int(full_rounds) > 0 and age <= int(full_rounds):
                continue
            path = _tool_path(msg)
            ct = _file_content_type(msg)
            rendered = render_head_tail_notice(
                msg.content, path=path,
                head_tokens=self.file_context_head_tokens,
                tail_tokens=self.file_context_tail_tokens,
                model=self.model,
                full_tokens=full_tokens,
            )
            header = (
                f"[{name} output abbreviated by file context policy; "
                f"content_type={ct or 'unknown'}; original_tokens={full_tokens}.]"
            )
            messages[idx] = _stamp(
                msg, f"{header}\n{rendered}", "file_context_policy",
                form="head_tail", cleared=False,
            )

    def _run_s2a(self, messages: list) -> None:
        """S2a runs before the S1 recency cut so an oversized output
        later degraded to ``reference`` already carries its ``llm_abstract``.
        Fail-soft: any S2a error → fall back to the deterministic abstract."""
        if self.s2a is None:
            return
        try:
            self.s2a.apply(messages)
        except Exception:
            pass

    def _run_head_tail(self, messages: list) -> None:
        """Render a fresh large tool output as head, tail, and notice
        from the FULL VFS body, so the agent sees the start+end + where the full is
        — NOT a bare ``data:None`` omit ("don't hide the original just because it's
        long"). DEFAULT, deterministic, no LLM.

        Gate: a not-cleared ToolMessage envelope, text/log content_type, recorded
        ``output.full_tokens > headtail_threshold`` (the producer omitted the inline
        data), and NO ``llm_abstract`` (S2a tier-4, if enabled, already replaced the
        middle with its gist — don't double-render). Reads the full body from VFS by
        ``output.path``; a VFS miss / no reader → leave the abstract+path envelope
        intact (fail-soft). The rendered head+tail goes into ``output.data`` and the
        form is stamped ``head_tail``. S1 ``_compact`` can still degrade it further
        once it ages out of the fresh window."""
        if self.vfs_reader is None:
            return
        for idx, msg in enumerate(messages):
            try:
                if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
                    continue
                if _cleared(msg):
                    continue
                ce = getattr(msg, "response_metadata", {}).get("context_editing", {}) or {}
                if ce.get("strategy") == "lifecycle_headtail":
                    continue  # idempotent: already head-tailed this pass
                env = parse_envelope(msg.content)
                if env is None or env.get("llm_abstract"):
                    continue
                ct = output_content_type(env)
                if ct is None or ct.lower() not in _HEADTAIL_CONTENT_TYPES:
                    continue
                full_tokens = output_full_tokens(env)
                if not isinstance(full_tokens, int) or full_tokens <= self.headtail_threshold:
                    continue
                out = env.get("output")
                if isinstance(out, dict) and out.get("data") is not None:
                    continue  # already inline (small) — not an omitted large body
                path = output_path(env)
                body = self.vfs_reader.read(path)
                if not isinstance(body, str) or not body:
                    continue  # VFS miss → keep abstract+path stub (fail-soft)
                rendered = render_head_tail_notice(
                    body, path=path, head_tokens=self.headtail_head_tokens,
                    tail_tokens=self.headtail_tail_tokens, model=self.model,
                    full_tokens=full_tokens)
                new_out = {**out, "data": rendered}
                new_env = {**env, "output": new_out}
                messages[idx] = _stamp(msg, json.dumps(new_env, ensure_ascii=False),
                                       "lifecycle_headtail", form="head_tail",
                                       cleared=False)
            except Exception:
                continue  # tier-2 is fail-soft per message; never breaks a turn

    def _record_tokens(self, messages: list) -> None:
        """Update ONLY the CURRENT ``form`` on every message's ``meta.tokens``
        Runs after the degradation passes so the stamped
        form is known.

        Tokens are now recorded ONCE at message creation by
        ``TokenRecordMiddleware`` (which persists them in the checkpointer); this
        compaction pass is READ-ONLY w.r.t. the original counts — it preserves
        the persisted ``raw``/``abstract``/``compressed`` and stamps only the
        ``form`` that this edit just produced. Preserving the original ``raw``
        through degradation is what keeps tokens-saved reporting correct and
        stops the per-turn recompute on the transient deep-copy.

        Fallback (``stamp_form``): a message with NO recorded tokens (old
        pre-fix history) is computed once from its current content. Fail-soft,
        message-by-message — one bad message can't abort the pass."""
        for msg in messages:
            try:
                stamp_form(msg, _current_form(msg), model=self.model)
            except Exception:
                continue
        self._record_form_projection(messages)

    def _record_form_projection(self, messages: list) -> None:
        if self.form_projection_holder is None:
            return
        projection: dict[str, dict[str, Any]] = {}
        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            mid = getattr(msg, "id", None)
            if not isinstance(mid, str) or not mid:
                continue
            form = _current_form(msg)
            token_field = _form_token_field(form)
            spec: dict[str, Any] = {
                "current_form": form,
                "token_field": token_field,
            }
            toks = message_tokens(msg)
            if isinstance(toks, dict) and isinstance(toks.get(token_field), int):
                spec["tokens"] = toks[token_field]
            name = _tool_name(msg)
            if isinstance(name, str) and name:
                spec["tool"] = name
            path = _tool_path(msg)
            if isinstance(path, str) and path:
                spec["path"] = path
            projection[mid] = spec
        self.form_projection_holder.clear()
        self.form_projection_holder.update(projection)

    def _sweep_keep_latest_tools(self, messages: list) -> None:
        latest: dict[tuple, int] = {}
        for idx, msg in enumerate(messages):
            if isinstance(msg, ToolMessage):
                key = _keep_latest_key(msg)
                if key is not None:
                    latest[key] = idx
        keep = set(latest.values())
        limit = max(1, int(self.max_node_specs or 5))
        node_spec_latest = [
            (key, idx) for key, idx in latest.items()
            if len(key) >= 2 and key[0] == "get_node_spec"
        ]
        if len(node_spec_latest) > limit:
            keep_node_specs = {
                idx for _key, idx in sorted(
                    node_spec_latest, key=lambda item: item[1], reverse=True
                )[:limit]
            }
            for _key, idx in node_spec_latest:
                if idx not in keep_node_specs:
                    keep.discard(idx)
        for idx, msg in enumerate(messages):
            if idx in keep or not isinstance(msg, ToolMessage) or _cleared(msg):
                continue
            name = _tool_name(msg)
            key = _keep_latest_key(msg)
            if key is None:
                continue
            breadcrumb = (
                f"<{name} output superseded; the latest result for this context is kept "
                "later in the conversation>"
            )
            messages[idx] = _stamp(msg, breadcrumb, "keep_latest_tool", form="reference")

    def _compact(
        self,
        messages: list,
        count_tokens: Any,
        rounds: dict[int, int],
        current_round: int,
    ) -> None:
        if count_tokens(messages) <= self.trigger:
            return
        cands = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
                continue
            if _cleared(msg):
                continue
            env = parse_envelope(msg.content)
            ct = output_content_type(env) if env else None
            cands.append((idx, msg, env, ct, policy_for(ct)))

        protected: set[int] = set()
        seen: dict[str | None, int] = {}
        for idx, _msg, _env, ct, pol in reversed(cands):
            if isinstance(ct, str) and ct.startswith(
                "application/vnd.vibecanvas.interactive"
            ):
                if current_round - rounds.get(idx, current_round) <= max(
                    0, int(self.interactive_artifact_protect_recent_rounds)
                ):
                    protected.add(idx)
                # Interactive artifacts use the turn-based window, not the
                # output-count-based fresh_k window below.
                continue
            n = seen.get(ct, 0)
            if n < pol.fresh_k:
                protected.add(idx)
                seen[ct] = n + 1

        before = count_tokens(messages)
        freed = 0
        degraded: list[dict[str, str | None]] = []
        for idx, msg, env, ct, pol in cands:
            if idx in protected:
                continue
            if before - freed <= self.trigger and freed >= self.clear_at_least:
                break
            old_tok = count_tokens([msg])
            messages[idx] = _stamp(msg, render_aged(env, msg.content, pol.aged_form),
                                   "lifecycle", form=pol.aged_form)
            freed += old_tok - count_tokens([messages[idx]])
            degraded.append({"content_type": ct, "form": pol.aged_form})

        if degraded:
            try:
                _slog.info("context_compaction", tokens_before=before,
                           tokens_after=before - freed, degraded=degraded,
                           count=len(degraded))
            except Exception:  # fail-soft: a logging error never breaks the turn
                pass

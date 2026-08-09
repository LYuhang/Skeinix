"""Per-message token accounting used to trigger compaction.

Every message records its tokenized length(s) under a ``tokens`` meta dict so the
running context size is a cheap running SUM, never a per-turn re-tokenize of the
whole history. This module is SUBSTRATE only — it does NOT decide or perform any
compaction (S1 lives in ``LifecyclePolicyEdit``; S2a/S2b are separate phases). It
just records the lengths the triggers will read.

Every message uses the following ``meta.tokens`` schema::

    tokens = {
        raw:        int,         # tokens of the FULL ORIGINAL content (required)
        abstract:   int | None,  # tokens of the deterministic S0 abstract (tool outputs only)
        head_tail:  int | None,  # tokens of deterministic head+tail view (file tools only)
        compressed: int | None,  # tokens of the S2a/S2b LLM form, once computed (reserved)
        form:       'raw'|'abstract'|'reference'|'minimal'|'head_tail'|'compressed',
        model:      str          # tokenizer that produced these counts
    }

Storage = message metadata (rides the checkpointer; NO new table / schema):
``response_metadata['tokens']`` for AIMessage/ToolMessage (which already carry
``response_metadata``), ``additional_kwargs['tokens']`` for HumanMessage.

Everything here is fail-soft: a token-count error must never break a turn.

Future refinement (out of scope): backfill ``raw``/``abstract`` on a background
task instead of synchronously. For now we compute synchronously-but-cheaply — the
approximate fallback (chars≈4) keeps it fast.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages.utils import count_tokens_approximately

from .middleware.compaction_forms import parse_envelope, render_head_tail_notice

# Valid values for a message's current compacted form.
FORMS = ("raw", "abstract", "reference", "minimal", "head_tail", "compressed")


# --------------------------------------------------------------------------- #
# count_tokens
# --------------------------------------------------------------------------- #

def count_tokens(text: str, model: str) -> int:
    """Count tokens in ``text`` for ``model``.

    Uses a real tokenizer when one is cheaply available for the model family,
    else falls back to langchain's ``count_tokens_approximately`` (chars≈4) — the
    SAME approximate counter the compaction middleware already uses, so the two
    agree. Fail-soft: any error in the real path degrades to the approximate
    count; the approximate path itself never raises for str input.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    enc = _tiktoken_for(model)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass  # fall through to the approximate counter
    return _approx(text)


def _approx(text: str) -> int:
    try:
        # count_tokens_approximately works over a message list; wrap a tiny
        # HumanMessage so we reuse the exact middleware-side heuristic.
        from langchain_core.messages import HumanMessage
        return int(count_tokens_approximately([HumanMessage(content=text)]))
    except Exception:
        # Last-resort chars≈4 (the documented approximation) — never raise.
        return max(0, len(text) // 4)


# tiktoken is only meaningful for OpenAI-family tokenizers. For any other
# provider (or if tiktoken is absent) we use the approximate counter. Cached so
# we don't re-resolve the encoding per call.
_TIKTOKEN_CACHE: dict[str, Any] = {}


def _tiktoken_for(model: str):
    """Return a tiktoken encoding for OpenAI-family models, else None.

    Conservative: only engages for models whose tokenizer tiktoken actually
    knows. Anything else (Gemini, Anthropic, Doubao/Ark, unknown) returns None →
    approximate fallback. Never raises.
    """
    if not model:
        return None
    if model in _TIKTOKEN_CACHE:
        return _TIKTOKEN_CACHE[model]
    enc = None
    try:
        # Provider-prefixed ids look like "openai:gpt-4o" / "google_genai:...".
        provider, _, bare = model.partition(":")
        bare = bare or provider
        prov = provider.lower()
        if ("openai" in prov) or prov in ("", "gpt") or bare.lower().startswith(("gpt", "o1", "o3", "o4")):
            import tiktoken  # local: optional dep, only on the OpenAI path
            try:
                enc = tiktoken.encoding_for_model(bare)
            except Exception:
                enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        enc = None
    _TIKTOKEN_CACHE[model] = enc
    return enc


# --------------------------------------------------------------------------- #
# build the tokens dict
# --------------------------------------------------------------------------- #

def build_message_tokens(
    content: str,
    *,
    model: str,
    form: str = "raw",
    artifact: dict | None = None,
) -> dict:
    """Build the ``meta.tokens`` dict for one message's string content.

    - ``raw`` = tokens of the full original content (always).
    - ``abstract`` = tokens of the deterministic S0 ``abstract`` string IF the
      content is a tool-output envelope; else None.
    - ``head_tail`` = tokens of the deterministic head+tail view for file tools.
    - ``compressed`` = None here (filled by S2a/S2b when they run).
    - ``form`` = the CURRENT effective form (caller-supplied; defaults 'raw').
    - ``model`` = the tokenizer that produced the counts.

    Fail-soft: a malformed / non-envelope string is treated as a plain message
    (raw only, abstract=None). Never raises.
    """
    if form not in FORMS:
        form = "raw"
    text = content if isinstance(content, str) else ("" if content is None else str(content))

    raw = count_tokens(text, model)

    abstract_tok: Optional[int] = None
    head_tail_tok: Optional[int] = None
    try:
        abstract_str = None
        if isinstance(artifact, dict):
            val = artifact.get("content_abstract")
            if isinstance(val, str):
                abstract_str = val
            elif isinstance(artifact.get("meta"), dict):
                toks = artifact["meta"].get("tokens")
                if isinstance(toks, dict) and isinstance(toks.get("content_abstract"), int):
                    abstract_tok = toks["content_abstract"]
        if abstract_str is None and abstract_tok is None:
            env = parse_envelope(text)
            if env is not None:
                abstract_str = env.get("abstract")
        if isinstance(abstract_str, str):
            abstract_tok = count_tokens(abstract_str, model)
    except Exception:
        abstract_tok = None

    try:
        if _is_file_tool_artifact(artifact) and text:
            path = _artifact_path(artifact)
            head_tokens, tail_tokens = _file_head_tail_budgets()
            rendered = render_head_tail_notice(
                text,
                path=path,
                head_tokens=head_tokens,
                tail_tokens=tail_tokens,
                model=model,
                full_tokens=raw,
            )
            head_tail_tok = count_tokens(rendered, model)
    except Exception:
        head_tail_tok = None

    return {
        "raw": raw,
        "abstract": abstract_tok,
        "head_tail": head_tail_tok,
        "compressed": None,
        "form": form,
        "model": model,
    }


def _is_file_tool_artifact(artifact: dict | None) -> bool:
    if not isinstance(artifact, dict):
        return False
    meta = artifact.get("meta")
    if not isinstance(meta, dict):
        return False
    return meta.get("tool") in {"read_file", "write_file", "edit_file"}


def _artifact_path(artifact: dict | None) -> str | None:
    if not isinstance(artifact, dict):
        return None
    body = artifact.get("artifact")
    if isinstance(body, dict):
        handles = body.get("handles")
        if isinstance(handles, dict) and isinstance(handles.get("path"), str):
            return handles["path"]
        target = body.get("target")
        if isinstance(target, dict) and isinstance(target.get("path"), str):
            return target["path"]
    payload = artifact.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("ref"), str):
        return payload["ref"]
    return None


def _file_head_tail_budgets() -> tuple[int, int]:
    try:
        from vibecanvas_api.config import config
        c = getattr(config.agent, "compaction_v2", None)
        return (
            int(getattr(c, "file_context_head_tokens", 2000)),
            int(getattr(c, "file_context_tail_tokens", 2000)),
        )
    except Exception:
        return 2000, 2000


# --------------------------------------------------------------------------- #
# attach / read meta.tokens on a message
# --------------------------------------------------------------------------- #

def _meta_slot(msg: Any) -> str:
    """HumanMessage carries metadata in ``additional_kwargs``; AI/Tool/System in
    ``response_metadata``. This matches the existing debug-metadata convention."""
    return "additional_kwargs" if type(msg).__name__ == "HumanMessage" else "response_metadata"


def message_tokens(msg: Any) -> Optional[dict]:
    """Return the recorded ``tokens`` dict for a message, or None if absent."""
    for slot in ("response_metadata", "additional_kwargs"):
        meta = getattr(msg, slot, None)
        if isinstance(meta, dict):
            tok = meta.get("tokens")
            if isinstance(tok, dict):
                return tok
    return None


def record_message_tokens(msg: Any, *, model: str, form: Optional[str] = None) -> None:
    """Record (or refresh) ``meta.tokens`` on ``msg`` in place. Fail-soft.

    Idempotent: re-recording the SAME ``(model, form)`` over identical content
    produces an identical dict (no drift) because ``build_message_tokens`` is a
    pure function of (content, model, form). When ``form`` changes (e.g. S1
    degrades raw→reference), the new form is stamped while ``raw`` is recomputed
    from the (degraded) current content — callers that need the original raw
    preserved should pass the form WITHOUT mutating content here, or rely on the
    LifecyclePolicyEdit seam which records BEFORE/with degradation.

    ``form`` defaults to: the existing recorded form if any, else 'raw'. This
    keeps a plain recording pass from clobbering a form S1 already stamped.
    """
    try:
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        existing = message_tokens(msg)
        if form is None:
            form = (existing or {}).get("form", "raw")
        artifact = getattr(msg, "artifact", None)
        tok = build_message_tokens(
            content,
            model=model,
            form=form,
            artifact=artifact if isinstance(artifact, dict) else None,
        )
        # Preserve previously-computed form counts when refreshing metadata.
        if isinstance(existing, dict) and isinstance(existing.get("head_tail"), int) \
                and tok.get("head_tail") is None:
            tok["head_tail"] = existing["head_tail"]

        # Preserve a previously-computed S2a/S2b `compressed` count: it is a
        # frozen-once property of the LLM gist, not derivable from `content`
        # (the gist lives in `llm_abstract`, not the message body), so a plain
        # re-record must not clobber it back to None.
        if isinstance(existing, dict) and isinstance(existing.get("compressed"), int) \
                and tok.get("compressed") is None:
            tok["compressed"] = existing["compressed"]

        slot = _meta_slot(msg)
        meta = getattr(msg, slot, None)
        if not isinstance(meta, dict):
            meta = {}
            try:
                setattr(msg, slot, meta)
            except Exception:
                return  # immutable / odd object — give up silently
        meta["tokens"] = tok
    except Exception:
        # Token accounting must never break a turn.
        return


def stamp_form(msg: Any, form: str, *, model: str) -> None:
    """Update ONLY the ``form`` on a message's PRE-RECORDED ``meta.tokens``,
    preserving the original ``raw``/``abstract``/``compressed``
    CORRECTION). Fail-soft.

    This is the read-only path the compaction middleware uses: tokens are
    recorded ONCE at message creation (``TokenRecordMiddleware``) on the real
    message; degradation only changes the CURRENT form, never the original
    counts (the original ``raw`` is needed for tokens-saved reporting and must
    survive degradation).

    Fallback: if the message has NO recorded tokens (old pre-fix history),
    delegate to ``record_message_tokens`` to compute them once from the current
    content (the prior P2a behaviour).
    """
    try:
        if form not in FORMS:
            form = "raw"
        existing = message_tokens(msg)
        if not (isinstance(existing, dict) and isinstance(existing.get("raw"), int)):
            # no usable record → compute once from current content
            record_message_tokens(msg, model=model, form=form)
            return
        if existing.get("form") == form:
            return  # already at this form — nothing to change (idempotent)
        slot = _meta_slot(msg)
        meta = getattr(msg, slot, None)
        if not isinstance(meta, dict):
            meta = {}
            try:
                setattr(msg, slot, meta)
            except Exception:
                return
        # mutate in place so the recorded raw/abstract/compressed are kept
        tok = dict(existing)
        tok["form"] = form
        meta["tokens"] = tok
    except Exception:
        # Token accounting must never break a turn.
        return


# --------------------------------------------------------------------------- #
# context-size estimate (what S1 / S2b triggers read)
# --------------------------------------------------------------------------- #

# Which recorded length each current form reads. 'raw'→raw; the deterministic S0
# abstract and S1 stub forms read 'abstract' (the cheapest recorded length that
# bounds the degraded body); the LLM forms read 'compressed'.
_FORM_FIELD = {
    "raw": "raw",
    "abstract": "abstract",
    "reference": "abstract",
    "minimal": "abstract",
    "head_tail": "head_tail",
    "compressed": "compressed",
}


def estimate_context_tokens(messages: list, *, model: str = "") -> int:
    """Σ over messages of the count of each message's CURRENT form.

    Reads ``meta.tokens[<field for current form>]``; if a message has no recorded
    tokens (or the field for its form is missing/None), falls back to counting its
    CURRENT content. Pure function over a message list; fail-soft per message.

    This is the cheap running-size number S1's budget and S2b's 80% trigger read.
    Because frozen forms have fixed recorded sizes, the sum only changes by
    appended turns → O(new turns) per turn rather than O(history) re-tokenize.
    """
    total = 0
    for msg in messages:
        total += _estimate_one(msg, model)
    return total


def _estimate_one(msg: Any, model: str) -> int:
    try:
        tok = message_tokens(msg)
        if isinstance(tok, dict):
            field = _FORM_FIELD.get(tok.get("form", "raw"), "raw")
            val = tok.get(field)
            if isinstance(val, int):
                return val
            # form recorded but its field is None (e.g. abstract not computed):
            # fall back to raw if present, else count the current content.
            if isinstance(tok.get("raw"), int):
                return tok["raw"]
        # No usable record → count the current content with the recorded model
        # if one is known, else the caller-supplied active model.
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        use_model = tok.get("model") if isinstance(tok, dict) and tok.get("model") else model
        return count_tokens(content, use_model)
    except Exception:
        return 0

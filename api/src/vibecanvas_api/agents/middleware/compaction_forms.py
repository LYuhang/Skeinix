"""Parse a tool-output envelope + render its three degraded forms.

All renderers are PURE functions of the (frozen) envelope/content — never of
current state — and emit byte-stable output (fixed key order, ensure_ascii=False)
so re-deriving a stub each turn is cache-safe.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def parse_envelope(content: str) -> Optional[dict]:
    """A tool-output envelope is a JSON object with 'status' and 'output' keys."""
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and "status" in obj and "output" in obj:
        return obj
    return None


def output_content_type(env: dict) -> Optional[str]:
    out = env.get("output")
    return out.get("content_type") if isinstance(out, dict) and isinstance(out.get("content_type"), str) else None


def output_path(env: dict) -> Optional[str]:
    out = env.get("output")
    return out.get("path") if isinstance(out, dict) and isinstance(out.get("path"), str) else None


def _render_reference(env: dict) -> str:
    out = env.get("output") or {}
    abstract = env.get("llm_abstract") or env.get("abstract") or ""
    stub_out: dict[str, Any] = {}
    if isinstance(out, dict):
        if out.get("path"):
            stub_out["path"] = out["path"]
        if out.get("content_type"):
            stub_out["content_type"] = out["content_type"]
    return json.dumps(
        {"status": env.get("status", "success"), "abstract": abstract,
         "output": stub_out or None},
        ensure_ascii=False,
    )


def _render_minimal(env: dict) -> str:
    path = output_path(env)
    return f"[output elided: {path}]" if path else "[output elided]"


def _render_head_tail(body: str, *, head: int = 20, tail: int = 20) -> str:
    lines = body.splitlines()
    if len(lines) <= head + tail:
        return body
    elided = len(lines) - head - tail
    return "\n".join([*lines[:head], f"…({elided} lines elided)…", *lines[-tail:]])


def output_full_tokens(env: dict) -> Optional[int]:
    """The recorded full size (in tokens) of a large, omitted output body.

    Producers that omit a large ``output.data`` record ``full_tokens`` on the
    output (envelope ``fill_output_data``) so the middleware can decide the
    in-context FORM by the FULL size, not the (omitted) inline data. None when
    absent (small/inline output, or an old envelope)."""
    out = env.get("output")
    if isinstance(out, dict) and isinstance(out.get("full_tokens"), int):
        return out["full_tokens"]
    return None


def render_head_tail_notice(body: str, *, path: Optional[str], head_tokens: int,
                            tail_tokens: int, model: str = "",
                            full_tokens: Optional[int] = None) -> str:
    """Build a deterministic head, tail, and notice form for a large output.

    This operation is pure and does not call an LLM.

        head(N tok) + "\\n…[{X} tokens elided; full at {path}, re-read with
        read_file]…\\n" + tail(M tok)

    Token budgets are honoured via the active model's tokenizer
    (``count_tokens``); a tiny body (≤ head+tail budget) is returned WHOLE.
    ``full_tokens`` (if given) is the original size for the notice; else it is
    measured from ``body``. Byte-stable for identical input (cache-safe).
    """
    from vibecanvas_api.agents.token_accounting import count_tokens

    total = full_tokens if isinstance(full_tokens, int) else count_tokens(body, model)
    if total <= head_tokens + tail_tokens:
        return body

    head = _take_tokens(body, head_tokens, model, from_end=False)
    tail = _take_tokens(body, tail_tokens, model, from_end=True)
    elided = max(0, total - count_tokens(head, model) - count_tokens(tail, model))
    where = f", full at {path}, re-read with read_file" if path else ""
    notice = f"\n…[{elided} tokens elided{where}]…\n"
    return f"{head}{notice}{tail}"


def render_head_tail_input(body: str, *, head_tokens: int, tail_tokens: int,
                           model: str = "", full_tokens: Optional[int] = None) -> str:
    """Head+tail projection for historical tool input strings.

    The argument remains the same type and same field: a string containing the
    original head and tail, with only the middle replaced by an omission marker.
    Unlike output compaction, it does not mention paths or read tools because it
    is a projection of a past input value.
    """
    from vibecanvas_api.agents.token_accounting import count_tokens

    total = full_tokens if isinstance(full_tokens, int) else count_tokens(body, model)
    if total <= head_tokens + tail_tokens:
        return body

    head = _take_tokens(body, head_tokens, model, from_end=False)
    tail = _take_tokens(body, tail_tokens, model, from_end=True)
    omitted = max(0, total - count_tokens(head, model) - count_tokens(tail, model))
    notice = f"\n...[{omitted} tokens omitted to save context]...\n"
    return f"{head}{notice}{tail}"


def _take_tokens(body: str, budget: int, model: str, *, from_end: bool) -> str:
    """Return the head (or tail) slice of ``body`` whose token count is ≤ budget.

    Char-binary-search over the tokenizer so we don't depend on a tokenizer's
    decode API — pure + tokenizer-agnostic + fail-soft. budget ≤ 0 → empty."""
    from vibecanvas_api.agents.token_accounting import count_tokens

    if budget <= 0 or not body:
        return ""
    if count_tokens(body, model) <= budget:
        return body
    lo, hi = 0, len(body)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        slice_ = body[-mid:] if from_end else body[:mid]
        if count_tokens(slice_, model) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return body[-best:] if from_end else body[:best]


def render_aged(env: Optional[dict], raw_content: str, aged_form: str) -> str:
    """Render the degraded form. `env` is None for non-envelope tool output."""
    if env is None:
        # Non-envelope tool output (e.g. a {"status":"error","error":...} return with
        # no `output` key). head_tail is LOSSLESS for short content (returns it whole
        # when under head+tail lines) and bounds huge blobs — never blanket-elide, or
        # we'd drop the agent's only record of WHY a prior op failed.
        return _render_head_tail(raw_content)
    if aged_form == "minimal":
        return _render_minimal(env)
    if aged_form == "head_tail":
        out = env.get("output") or {}
        data = out.get("data") if isinstance(out, dict) else None
        body = data if isinstance(data, str) else (
            json.dumps(data, ensure_ascii=False) if data is not None else "")
        return _render_head_tail(body) if body else _render_reference(env)
    return _render_reference(env)

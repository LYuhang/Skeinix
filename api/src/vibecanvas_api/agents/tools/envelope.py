"""Envelope v2 — the named form-ladder contract (spec 2026-06-22 §3).

A single tool output carries a degradation ladder:

    content > content_abbreviation > content_abstract > ref   (deterministic)
                                        ⌎─ content_compress    (LLM, selective)
    auxiliary  (multimodal lane)

This module owns the DETERMINISTIC producers — the `make_envelope` builder (fixed
key order + ensure_ascii=False for byte-stable, prefix-cache-friendly output), the
`content_type`-keyed **abbreviation** scheme (head/tail), and the `content_type`
default + **tool-name override** **abstract** scheme. The LLM-based
`content_compress` and per-turn form selection live in the compaction middleware,
not here.
"""
from __future__ import annotations

import json
from typing import Callable

# Fixed key order ensures identical logical output produces identical bytes.
ENVELOPE_KEY_ORDER = [
    "status", "error",
    "content", "content_abbreviation", "content_abstract", "content_compress",
    "auxiliary", "output_meta",
]


def make_envelope(
    *,
    status: str,
    error: str | None,
    content,
    content_abbreviation: str | None = None,
    content_abstract: str | None = None,
    content_compress: str | None = None,
    auxiliary: list | None = None,
    output_meta: dict | None = None,
) -> dict:
    """Build the canonical envelope dictionary in fixed key order.

    Returns a plain dict (the caller serialises with ensure_ascii=False). Key
    order is pinned via ENVELOPE_KEY_ORDER so two logically-identical outputs
    serialise to identical bytes.
    """
    raw = {
        "status": status,
        "error": error,
        "content": content,
        "content_abbreviation": content_abbreviation,
        "content_abstract": content_abstract,
        "content_compress": content_compress,
        "auxiliary": auxiliary if auxiliary is not None else [],
        "output_meta": output_meta or {},
    }
    return {k: raw[k] for k in ENVELOPE_KEY_ORDER}


def dumps(env: dict) -> str:
    """Serialise an envelope byte-stably."""
    return json.dumps(env, ensure_ascii=False)


# ───────────────────────── token estimate (chars≈4) ─────────────────────────
def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _head_tail_chars(head_tokens: int, tail_tokens: int) -> tuple[int, int]:
    return head_tokens * 4, tail_tokens * 4


# ───────────────────────── abbreviation schemes (by content_type) ────────────
def _abbrev_text(body: str, head_chars: int, tail_chars: int, path: str | None) -> str:
    if len(body) <= head_chars + tail_chars:
        return body
    head, tail = body[:head_chars], body[-tail_chars:]
    where = f", full at {path}, re-read with read_file" if path else ""
    return f"{head}\n…[{approx_tokens(body[head_chars:-tail_chars])} tokens elided{where}]…\n{tail}"


def _abbrev_json(body: str, head_chars: int, tail_chars: int, path: str | None) -> str:
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return _abbrev_text(body, head_chars, tail_chars, path)
    if isinstance(obj, dict):
        keys = list(obj.keys())
        where = f"; full at {path}" if path else ""
        return f"{{json object, top-level keys: {keys}{where}}}"
    if isinstance(obj, list):
        where = f"; full at {path}" if path else ""
        return f"[json array, {len(obj)} items{where}]"
    return _abbrev_text(body, head_chars, tail_chars, path)


def _abbrev_table(body: str, head_chars: int, tail_chars: int, path: str | None) -> str:
    lines = body.splitlines()
    keep = max(1, head_chars // 80)
    if len(lines) <= keep + 1:
        return body
    where = f"; full at {path}" if path else ""
    return "\n".join(lines[:keep]) + f"\n…[{len(lines) - keep} more rows{where}]…"


def abbreviate(content, content_type: str, *, head_tokens: int, tail_tokens: int,
               path: str | None = None) -> str:
    """Build a deterministic head/tail abbreviation based on content type.

    text/* and unknown → head+tail+notice; application/json → top-level keys;
    table/* → first rows. Never calls an LLM.
    """
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    head_chars, tail_chars = _head_tail_chars(head_tokens, tail_tokens)
    ct = (content_type or "").lower()
    if ct == "application/json" or ct == "json":
        return _abbrev_json(body, head_chars, tail_chars, path)
    if ct.startswith("table/"):
        return _abbrev_table(body, head_chars, tail_chars, path)
    return _abbrev_text(body, head_chars, tail_chars, path)


# ───────────────────────── abstract schemes (by content_type + tool override) ─
_ABSTRACT_SCHEMES: dict[str, Callable[[str, dict], str]] = {}


def register_abstract_scheme(key: str, fn: Callable[[str, dict], str]) -> None:
    """Register an abstract scheme by tool name (override) or content_type."""
    _ABSTRACT_SCHEMES[key] = fn


def _lines(body: str) -> int:
    return body.count("\n") + 1 if body else 0


def _abstract_shell(body: str, extras: dict) -> str:
    exit_code = extras.get("exit_code")
    ec = f", exit {exit_code}" if exit_code is not None else ""
    return f"shell output{ec}, {_lines(body)} lines"


def _abstract_table(body: str, extras: dict) -> str:
    rows = extras.get("rows", _lines(body))
    return f"table, {rows} rows"


def _abstract_default(body: str, extras: dict) -> str:
    return f"{extras.get('content_type', 'text')} output, {_lines(body)} lines, {approx_tokens(body)} tokens"


_CONTENT_TYPE_ABSTRACT: dict[str, Callable[[str, dict], str]] = {
    "text/shell": _abstract_shell,
    "table/jsonl": _abstract_table,
    "table/csv": _abstract_table,
    "table/tsv": _abstract_table,
}


def abstract_of(content, content_type: str, *, extras: dict | None = None,
                tool: str | None = None) -> str:
    """One-line abstract. Tool-name override wins; else content_type default;
    otherwise the generic size-based default."""
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    extras = dict(extras or {})
    extras.setdefault("content_type", content_type)
    if tool and tool in _ABSTRACT_SCHEMES:
        return _ABSTRACT_SCHEMES[tool](body, extras)
    ct = (content_type or "").lower()
    if ct in _ABSTRACT_SCHEMES:
        return _ABSTRACT_SCHEMES[ct](body, extras)
    if ct in _CONTENT_TYPE_ABSTRACT:
        return _CONTENT_TYPE_ABSTRACT[ct](body, extras)
    return _abstract_default(body, extras)


def as_ref(pointer: str) -> str:
    """Return the thinnest deterministic form: a bare pointer string."""
    return pointer

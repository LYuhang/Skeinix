"""Canonical tool-output envelope (VFS 2b-2a).

{status, error, abstract, output} with a FIXED key order + consistent
ensure_ascii so identical logical output serialises to identical bytes
for prompt-cache stability. It lives outside storage/ref_helpers.py
because 2b-3 deletes that module.
"""
from __future__ import annotations

import json

# Shared inline-data cap: producers inline output.data up to CAP_ROWS rows when
# fresh; read_file's artifact read defaults to the same line limit.
CAP_ROWS = 50


def tool_ok(abstract: str, output) -> str:
    return json.dumps(
        {"status": "success", "error": None, "abstract": abstract, "output": output},
        ensure_ascii=False,
    )


def tool_err(error: str, abstract: str = "") -> str:
    return json.dumps(
        {"status": "error", "error": error, "abstract": abstract, "output": None},
        ensure_ascii=False,
    )


# Inline cap (chars) for fresh-small `output.data`. == read_file's _MAX_CHARS.
INLINE_CHARS = 16000


def _serialize(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _inline_or_omit(value):
    """Return `value` for inline `output.data` when small enough to keep in
    context, else None (omit — the agent reads the file via read_file). Mirrors
    the full-when-fresh rule; cap = read_file's _MAX_CHARS = 16000."""
    return value if len(_serialize(value)) <= INLINE_CHARS else None


def fill_output_data(out: dict, value) -> dict:
    """Set the in-context ``output.data`` without omitting the full body.

    FRESH-small (serialized ≤ INLINE_CHARS) → inline `data` (unchanged rule).
    FRESH-large → do NOT inline (would bust context) BUT do NOT just hide it
    either: record ``full_chars`` (and an approximate ``full_tokens``) on the
    output so the compaction middleware can decide the in-context FORM (head+tail
    / cleared / S2a-gist) by reading the FULL body back from VFS via ``path`` —
    instead of the agent seeing a bare ``data:None`` with no signal that a large
    body even exists. The producer has ALREADY written the full body to VFS at
    ``out['path']`` before calling this, so the middleware can re-hydrate.

    Mutates and returns ``out`` (the envelope's ``output`` dict). Pure aside from
    that mutation; byte-stable for identical input.
    """
    serialized = _serialize(value)
    n = len(serialized)
    if n <= INLINE_CHARS:
        out["data"] = value
        return out
    # Large: omit inline data, but record the full size so the middleware re-hydrates.
    out["full_chars"] = n
    out["full_tokens"] = max(1, n // 4)  # chars≈4 approximation (count_tokens_approximately)
    return out

"""JSON Pointer (RFC 6901) parsing + workflow-aware resolution.

Pure module — no I/O, no global state, no codebase-specific imports. Only
`parse` and `resolve` are needed by the current workflow edit implementation.
"""
from __future__ import annotations

import re
from typing import Any, Tuple


def parse(path: str) -> list[str]:
    """JSON Pointer string -> list of decoded segments.

    Empty pointer "" yields []. Each segment has ~1 decoded to "/" and
    ~0 decoded to "~" (RFC 6901 §4). Raises ValueError if the pointer is
    non-empty and doesn't start with "/".
    """
    if path == "":
        return []
    if not path.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {path!r}")
    return [
        seg.replace("~1", "/").replace("~0", "~")
        for seg in path[1:].split("/")
    ]


def resolve(doc: Any, path: str) -> Tuple[Any, Any, bool]:
    """Walk `doc` along the JSON Pointer; return (parent, key, exists).

    parent  — the container (dict or list) that holds the target.
    key     — str for dict membership, int for list index, or the literal
              '-' sentinel for "append to end of list".
    exists  — True iff parent[key] is currently set. Always False when
              key is '-' or a list index out of range.

    Raises ValueError (empty path; non-int/'-' list index at the tail),
    or KeyError / IndexError / TypeError for intermediate-segment failures.
    """
    segs = parse(path)
    if not segs:
        raise ValueError("empty pointer not allowed at resolve layer")
    target: Any = doc
    for s in segs[:-1]:
        if isinstance(target, list):
            target = target[int(s)]  # may raise IndexError / ValueError
        else:
            target = target[s]       # may raise KeyError / TypeError
    last = segs[-1]
    if isinstance(target, list):
        if last == "-":
            return target, "-", False
        if not re.fullmatch(r"\d+", last):
            raise ValueError(
                f"list index must be integer or '-', got {last!r} at end of {path!r}"
            )
        idx = int(last)
        return target, idx, 0 <= idx < len(target)
    if isinstance(target, dict):
        return target, last, last in target
    raise TypeError(
        f"path {path!r} resolves through a {type(target).__name__}; "
        f"expected dict or list at parent level"
    )

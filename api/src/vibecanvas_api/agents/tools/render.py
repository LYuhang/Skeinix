"""Per-tool output RENDER registry (spec 2026-06-22 §2.1, §6.2).

The decorator stays GENERIC — it knows no specific tool. Each tool's presentation
strategy is a `render(raw, ctx) -> Rendered` registered into ONE registry, keyed by
tool name; the override CODE is co-located with the tool's own module (it registers
itself at import). Uniform dispatch signature, tool-specific handler body.

- Most tools register NOTHING → the `default_render` (content_type-generic abstract,
  raw serialized as content) handles them for free.
- A tool that needs domain-specific layout (e.g. `get_workflow` pulling
  `process_fn`/`prompt_template` out of the JSON into a `field_path`-headed section)
  registers a `render` whose body knows its own `raw` type.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from vibecanvas_api.agents.tools.envelope import abstract_of


@dataclass
class RenderCtx:
    """The uniform context the decorator hands every render scheme."""
    tool: str | None
    content_type: str
    extras: dict


@dataclass
class Rendered:
    """The uniform output every render scheme returns."""
    content: Any                       # display content (str or serializable; placeholders if laid-out)
    content_type: str
    abstract: str | None = None
    auxiliary: list | None = None      # MEDIA only
    extras: dict | None = None         # chaining handles → artifact.handles
    path: str | None = None            # a DOMAIN re-fetch path the tool already persisted to
                                       # (e.g. node_execute's /run result file). When set, the
                                       # render's abstract should mention it; the decorator won't
                                       # auto-append a "(full body at …)" note.


# tool-name → render fn. Uniform signature: (raw: Any, ctx: RenderCtx) -> Rendered.
_RENDER_REGISTRY: dict[str, Callable[[Any, RenderCtx], Rendered]] = {}


def register_render(tool: str):
    """Decorator: register a tool's render override (co-located in the tool's module)."""
    def deco(fn: Callable[[Any, RenderCtx], Rendered]):
        _RENDER_REGISTRY[tool] = fn
        return fn
    return deco


def get_render(tool: str | None) -> Callable[[Any, RenderCtx], Rendered] | None:
    return _RENDER_REGISTRY.get(tool) if tool else None


def _serialize(content) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


def default_render(raw: Any, ctx: RenderCtx) -> Rendered:
    """The generic path (no override): raw as content, content_type-keyed abstract."""
    return Rendered(
        content=raw,
        content_type=ctx.content_type,
        abstract=abstract_of(_serialize(raw), ctx.content_type, extras=ctx.extras, tool=ctx.tool),
        extras=ctx.extras,
    )


def render(raw: Any, ctx: RenderCtx) -> Rendered:
    """Dispatch: a tool's registered override if present, else the generic default."""
    fn = get_render(ctx.tool)
    return fn(raw, ctx) if fn else default_render(raw, ctx)


# ── shared layout helpers (§6.2) — ONE place owns the field-section convention ──
FIELDS_SEPARATOR = "----------------- fields ----------------"


def field_block(field_path: str, content_type: str, body: str) -> str:
    """One extracted field, headed by its `field_path` (the edit address)."""
    return f"[{field_path}]  ({content_type})\n{body}"


def with_fields(structural: str, fields: list[str]) -> str:
    """Append a `field_block` section after the structural content under the
    standard separator. Empty `fields` → structural unchanged. Layout schemes use
    this so the separator/convention lives in ONE place."""
    if not fields:
        return structural
    return structural + "\n\n" + FIELDS_SEPARATOR + "\n" + "\n\n".join(fields)

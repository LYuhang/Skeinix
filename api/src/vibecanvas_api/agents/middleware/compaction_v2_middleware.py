"""Form-ladder compaction — message adapter (spec 2026-06-22 §4, §7).

Bridges real LangChain messages ↔ the pure ``compaction_engine`` items, runs the
§4.0 ordered pipeline, and renders a PROJECTED message list (deep enough that the
originals are never mutated — invariant 1). The renderer rebuilds each degraded
ToolMessage's envelope keeping only the selected rung, so the fed view shrinks while
the frontend still receives a valid envelope.

Wired into ``agent.py`` behind the resolved Context v2 rollout mode. Shadow
mode remains observational; active LangChain canaries replace the legacy edit.
"""
from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from vibecanvas_api.agents.tools.envelope import make_envelope, dumps
from vibecanvas_api.agents.middleware import compaction_engine as ce
from vibecanvas_api.agents.middleware.meta_tokens import new_meta, current_form


def _parse(content) -> dict | None:
    if not isinstance(content, str):
        return None
    try:
        env = json.loads(content)
    except (ValueError, TypeError):
        return None
    return env if isinstance(env, dict) and "status" in env else None


def _meta_of(msg):
    ak = getattr(msg, "additional_kwargs", None) or {}
    if "_meta" in ak:
        return ak["_meta"]
    rm = getattr(msg, "response_metadata", None) or {}
    return rm.get("_meta")


def _reduced_envelope(env: dict, form: str) -> dict:
    """Rebuild the envelope keeping only the selected rung (+ the always-cheap
    abstract as a floor). output_meta/auxiliary are preserved (refs stay reachable)."""
    om = env.get("output_meta") or {}
    if not om and isinstance(env.get("output"), dict):
        om = {
            "path": env["output"].get("path"),
            "content_type": env["output"].get("content_type"),
        }
    abstract = env.get("content_abstract") or env.get("abstract")
    content = abbreviation = compress = None
    if form == "content_abbreviation":
        abbreviation = env.get("content_abbreviation")
    elif form == "content_compress":
        compress = env.get("content_compress") or abstract
    elif form == "ref":
        abstract = f"[ref → {om.get('path') or 'see latest'}]"
    return make_envelope(
        status=env.get("status", "success"), error=env.get("error"),
        content=content, content_abbreviation=abbreviation,
        content_abstract=abstract, content_compress=compress,
        auxiliary=env.get("auxiliary"), output_meta=om,
    )


def _render_msg(msg, env: dict, form: str):
    if form == "content":
        return msg  # unchanged → byte-identical → KV-cache stable
    new_content = dumps(_reduced_envelope(env, form))
    try:
        return msg.model_copy(update={"content": new_content})
    except Exception:  # noqa: BLE001 — fallback for non-pydantic carriers
        return ToolMessage(content=new_content, tool_call_id=getattr(msg, "tool_call_id", ""),
                           name=getattr(msg, "name", ""),
                           additional_kwargs=getattr(msg, "additional_kwargs", {}) or {})


def project_messages(messages: list, *, current_round: int, window: int, cfg):
    """Project a message list through the form-ladder compaction. Returns
    ``(projected_messages, plan)``; ``plan`` carries the LLM-step plans
    (content_compress candidates, B2). Originals are never mutated."""
    items = []
    idx_map = []
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            env = _parse(msg.content)
            if env is None:
                continue
            meta = _meta_of(msg) or new_meta(f"m{i}", 0)
            om = env.get("output_meta") or {}
            items.append({
                "meta": meta,
                "is_error": env.get("status") == "error",
                "tool": om.get("tool"),
                "path": om.get("path"),
                "stale": bool(om.get("stale_on_reread")),
                "role": "tool",
                "env": env,
            })
            idx_map.append(i)

    plan = ce.project(items, current_round=current_round, window=window, cfg=cfg)

    out = []
    j = 0
    for i, msg in enumerate(messages):
        if j < len(idx_map) and idx_map[j] == i:
            item = items[j]
            j += 1
            out.append(_render_msg(msg, item["env"], current_form(item["meta"])))
        else:
            out.append(msg)
    return out, plan

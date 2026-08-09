"""FormLadderEdit — the ContextEditingMiddleware edit that drives form-ladder v2.

Conforms to the same edit contract as ``LifecyclePolicyEdit`` (``apply(messages, *,
count_tokens) -> None``, mutating the per-call deep copy). Gated in ``agent.py``:
only added to the edit chain when ``config.agent.compaction_v2.v2_enabled`` — so when
off the live path is byte-for-byte unchanged.

It (1) assigns ``round_index`` from human-turn boundaries onto each message's
``_meta`` (synthesizing missing ones), (2) stamps ``tokens.content`` via the
middleware's ``count_tokens``, (3) runs the §4.0 projection in place. The B2 /
content_compress LLM calls (the ``plan``) are left for the C4/C5 wire-up; the
deterministic selection (supersession / A / B1) applies now.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, ToolMessage

from vibecanvas_api.agents.middleware.meta_tokens import (
    current_form,
    new_meta,
    stamp_tokens,
)
from vibecanvas_api.agents.middleware import compaction_v2_middleware as cv2


class FormLadderEdit:
    def __init__(self, agent_cfg, *, decision_holder: dict | None = None):
        raw_cfg = (
            agent_cfg.get("compaction_v2")
            if isinstance(agent_cfg, dict)
            else getattr(agent_cfg, "compaction_v2", agent_cfg)
        )
        if isinstance(raw_cfg, dict):
            from vibecanvas_api.config import CompactionV2Config
            raw_cfg = CompactionV2Config(raw_cfg)
        self.cfg = raw_cfg
        self.window = self.cfg.window_tokens
        self.decision_holder = decision_holder

    # ── helpers ──
    @staticmethod
    def _get_meta(msg):
        ak = getattr(msg, "additional_kwargs", None)
        if ak is None:
            return None
        return ak.get("_meta")

    def _ensure_round_and_tokens(self, messages, count_tokens) -> int:
        """Assign round_index from human turns; stamp tokens.content where missing.
        Returns the current (latest) round index."""
        round_index = -1
        for msg in messages:
            if isinstance(msg, HumanMessage):
                round_index += 1
            ak = getattr(msg, "additional_kwargs", None)
            if ak is None:
                continue
            meta = ak.get("_meta")
            if meta is None:
                meta = new_meta(f"auto_{id(msg)}", max(0, round_index))
                ak["_meta"] = meta
            else:
                # keep an explicit round_index if upstream set one; else assign
                if meta.get("round_index") is None:
                    meta["round_index"] = max(0, round_index)
            if isinstance(msg, ToolMessage):
                artifact = getattr(msg, "artifact", None)
                artifact_meta = artifact.get("meta") if isinstance(artifact, dict) else None
                interactive_protect = (
                    artifact_meta.get("protect_recent_rounds")
                    if isinstance(artifact_meta, dict)
                    else None
                )
                if isinstance(interactive_protect, int):
                    meta["protect_recent_rounds"] = max(0, interactive_protect)
                if (meta.get("tokens", {}) or {}).get("content") is None:
                    stamp_tokens(meta, "content", count_tokens([msg]))
        return max(0, round_index)

    def apply(self, messages: list, *, count_tokens) -> None:
        current_round = self._ensure_round_and_tokens(messages, count_tokens)
        before: dict[str, dict] = {}
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            meta = self._get_meta(message) or {}
            before[str(getattr(message, "id", "") or f"index:{index}")] = {
                "form": current_form(meta),
                "tokens": dict(meta.get("tokens") or {}),
            }
        projected, plan = cv2.project_messages(
            messages, current_round=current_round, window=self.window, cfg=self.cfg)
        messages[:] = projected
        if self.decision_holder is not None:
            decisions: list[dict] = []
            for index, message in enumerate(messages):
                if not isinstance(message, ToolMessage):
                    continue
                key = str(getattr(message, "id", "") or f"index:{index}")
                meta = self._get_meta(message) or {}
                after_form = current_form(meta)
                prior = before.get(key, {"form": "content", "tokens": {}})
                action = "included" if prior["form"] == after_form else "compacted"
                decisions.append({
                    "section_id": f"tool_result:{key}",
                    "action": action,
                    "reason": (
                        "within current form-ladder budget"
                        if action == "included"
                        else "form ladder age/pressure policy"
                    ),
                    "before_form": prior["form"],
                    "after_form": after_form,
                    "token_slots": dict(meta.get("tokens") or prior["tokens"]),
                })
            self.decision_holder["context_decisions"] = decisions
            self.decision_holder["context_compaction_plan"] = plan

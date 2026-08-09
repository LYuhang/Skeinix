"""ImageInjectionMiddleware — turn staged images into multimodal content the model sees.

The ``read_images`` tool stages decoded images on ``AgentContext.pending_images``.
Before the next model call we drain that staging area and inject the image content
blocks (langchain-core standard multimodal blocks, provider-agnostic).

INJECTION STRATEGY — two paths, chosen by provider:

• Anthropic / OpenAI (default): inject ONE new HumanMessage of image content
  blocks. For Anthropic, ``_merge_messages`` automatically merges the preceding
  ToolMessage + this HumanMessage into a single ``user`` turn. OpenAI's
  ``tool → user`` message sequence is valid per the Chat Completions protocol.
  HumanMessage images are NOT placed in the ToolMessage because OpenAI only
  accepts text content in tool-role messages.

• Google / Gemini: Gemini's formatter filters all ToolMessages out of the main
  message loop and pairs them with the preceding AIMessage as one
  ``Content(role="user", parts=[function_response, ...])``. A separate
  HumanMessage would create a second consecutive ``user`` Content — which the
  Gemini API rejects. Fix: merge the image blocks INTO the preceding ToolMessage
  so ``langchain_google_genai._convert_tool_message_to_parts`` emits the
  function_response Part alongside the image Parts in a single
  ``Content(role="user", ...)``.

The injected/merged message PERSISTS in the checkpointer — the model keeps
seeing the image on later steps (v1: simple retention).

Token accounting (HumanMessage path): ``meta.tokens`` is PRE-STAMPED with the
pixel-based image cost (Σ each image's w*h/(32*32), staged by the tool) so the
running-context estimate uses that — NOT a tokenization of the base64 blob.

Fail-soft: never raises into a turn.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage


def _is_google_provider(ctx) -> bool:
    """Return True when the active LLM is a Google/Gemini model."""
    agent_cfg = getattr(ctx, "agent_cfg", None)
    if agent_cfg is None:
        return False
    if hasattr(agent_cfg, "model"):
        model_str = agent_cfg.model or ""
    elif isinstance(agent_cfg, dict):
        model_str = agent_cfg.get("model", "") or ""
    else:
        return False
    provider = (model_str.split(":", 1)[0] if ":" in model_str else "").lower()
    return provider in ("google_genai", "google", "gemini", "vertexai")


class ImageInjectionMiddleware(AgentMiddleware):
    def before_model(self, state: Any, runtime: Any = None) -> Any:  # noqa: ANN401
        try:
            ctx = getattr(runtime, "context", None)
            pending = list(getattr(ctx, "pending_images", None) or [])
            if not pending:
                return None

            blocks: list = [{"type": "text",
                             "text": "Here are the image(s) you asked to see:"}]
            total_tokens = 0
            for img in pending:
                blocks.append({"type": "image", "source_type": "base64",
                               "mime_type": img.get("mime", "image/png"),
                               "data": img["b64"]})
                path = img.get("path")
                if path:
                    blocks.append({"type": "text", "text": path})
                total_tokens += int(img.get("tokens") or 0)

            # Drain so we inject exactly once (the appended message persists).
            try:
                ctx.pending_images = []
            except Exception:
                try:
                    ctx.pending_images.clear()
                except Exception:
                    pass

            messages = (state.get("messages", []) if isinstance(state, dict)
                        else getattr(state, "messages", []) or [])
            last = messages[-1] if messages else None

            # Google/Gemini path: merge image blocks into the preceding ToolMessage.
            # Gemini rejects consecutive same-role Contents; merging keeps the
            # function_response and image Parts in one Content(role="user", ...).
            # langchain_google_genai._convert_tool_message_to_parts separates
            # is_data_content_block entries into image Parts alongside the
            # FunctionResponse Part, all within a single user Content.
            if isinstance(last, ToolMessage) and _is_google_provider(ctx):
                orig = last.content
                if isinstance(orig, str):
                    merged: list = [{"type": "text", "text": orig}] + blocks
                else:
                    merged = list(orig) + blocks
                replacement = ToolMessage(
                    content=merged,
                    tool_call_id=last.tool_call_id,
                    name=last.name,
                    id=last.id,
                )
                return {"messages": [replacement]}

            # Default path (Anthropic, OpenAI): inject a new HumanMessage.
            # Anthropic's _merge_messages merges ToolMessage + HumanMessage into
            # one user turn; OpenAI's tool→user sequence is protocol-valid.
            msg = HumanMessage(content=blocks)
            # Pre-stamp the pixel-based token cost so TokenRecordMiddleware (which
            # only stamps when meta.tokens is absent) leaves it alone and the
            # estimate never tokenizes the base64 body.
            msg.additional_kwargs["tokens"] = {
                "raw": max(1, total_tokens), "abstract": None, "compressed": None,
                "form": "raw", "model": "image-pixels",
            }
            return {"messages": [msg]}
        except Exception:  # fail-soft — image injection must never break a turn
            return None

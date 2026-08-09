# -*- coding: utf-8 -*-
"""Plan C C1 — SubAgentCore: the thin shared bounded-agent runner.

A SubAgent is a bounded ``create_agent`` ReAct loop driven to a STRUCTURED
result by a TERMINAL ``set_output`` tool (``return_direct=True`` — calling it
ends the loop). This module is the single runner shared by the workflow engine
node and the agent-as-tool adapter (sibling tasks). It is deliberately thin:
build the agent, seed system+user messages, invoke under a recursion bound, and
read the staged structured output back off the context.

Contract:
  * The terminal tool stages its (already field-coerced) result onto
    ``ctx.staged_subagent_output``.
  * A clean run where the tool fired   → status="done"   + that output.
  * A clean run that stopped WITHOUT it → status="incomplete" + empty fields.
  * A pre-set ``ctx.stop_event`` (run-level cancellation) short-circuits BEFORE
    the model is invoked → status="error", error="cancelled", empty fields.
  * Any exception (bounded-loop overrun / model error) is CAUGHT and reported,
    NEVER raised out of ``run_bounded_agent``. If the tool happened to fire
    before the error, we still surface its output as "done".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ContextEditingMiddleware
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from pydantic import Field, create_model

from vibecanvas_api.agent import AgentContext
from vibecanvas_api.agents.middleware.lifecycle_policy import LifecyclePolicyEdit
from vibecanvas_api.agents.middleware.runtime_resilience import RuntimeResilienceMiddleware
from vibecanvas_api.agents.middleware.user_approval import denied_tool_message
from vibecanvas_api.agents.tools.subagent.output import coerce_to_fields


@dataclass
class SubAgentResult:
    status: str  # "done" | "incomplete" | "error"
    output: dict
    trace: list[dict] = field(default_factory=list)
    error: Optional[str] = None


_TYPE_MAP: dict[str, type] = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "number": float,
    "float": float,
    "boolean": bool,
    "bool": bool,
}

PLAN_SUBAGENT_APPROVAL_TOOLS = frozenset({"write_file", "edit_file", "bash"})


class PlanSubagentApprovalMiddleware(AgentMiddleware):
    """Independent pre-execution gate for detached Plan workers."""

    def __init__(self, request_approval: Callable[[str, str, dict], Awaitable[str]]):
        self.request_approval = request_approval

    async def awrap_tool_call(self, request: Any, handler):
        tool_call = request.tool_call or {}
        tool_name = str(tool_call.get("name") or "")
        if tool_name not in PLAN_SUBAGENT_APPROVAL_TOOLS:
            return await handler(request)
        tool_call_id = str(tool_call.get("id") or "")
        args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        decision = await self.request_approval(tool_name, tool_call_id, args)
        if decision == "approved":
            return await handler(request)
        return denied_tool_message(
            tool_name,
            tool_call_id,
            reason="user_denied" if decision == "denied" else "cancelled",
        )


def _message_text(msg: Any) -> str:
    """Flatten a message's text, tolerating ``.text`` being a property / method
    / str, and falling back to ``.content`` (str or typed-block list)."""
    text_attr = getattr(msg, "text", None)
    # ``.text`` may be a str (already flattened), a property, or — on older
    # message classes — a deprecated method. Use it only when it is already a
    # plain string; otherwise fall through to ``.content`` to avoid invoking the
    # deprecated callable form.
    if isinstance(text_attr, str) and text_attr:
        return text_attr

    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _messages_to_trace(messages: list) -> list[dict]:
    """Flatten agent messages to a JSON-friendly trace of
    ``{"role", "text", "tool_calls":[{"name","args"}]}``."""
    trace: list[dict] = []
    for msg in messages:
        role = getattr(msg, "type", None) or getattr(msg, "role", "") or ""
        raw_calls = getattr(msg, "tool_calls", None) or []
        tool_calls = []
        for call in raw_calls:
            if isinstance(call, dict):
                tool_calls.append({
                    "name": call.get("name", ""),
                    "args": call.get("args", {}),
                })
            else:
                tool_calls.append({
                    "name": getattr(call, "name", ""),
                    "args": getattr(call, "args", {}),
                })
        trace.append({
            "role": role,
            "text": _message_text(msg),
            "tool_calls": tool_calls,
        })
    return trace


def _compose_system_message(system_prompt: str, output_fields: dict, output_tool_name: str) -> str:
    """Append the auto-generated output contract to the author-written system prompt.

    The SubAgentNode's declared ``output_fields`` are the node's contract with
    the rest of the workflow, so the runtime — not the workflow author — tells
    the sub-agent exactly what to produce and how to finish. The author only
    has to describe the task in ``system_prompt``; the field list (with each
    field's description) and the finish instruction are added here.
    """
    base = (system_prompt or "").rstrip()
    if not output_fields:
        return base
    lines = []
    for name, spec in output_fields.items():
        spec = spec if isinstance(spec, dict) else {}
        ftype = spec.get("type", "")
        desc = spec.get("description", "")
        suffix = f": {desc}" if desc else ""
        lines.append(f"- {name}" + (f" ({ftype})" if ftype else "") + suffix)
    contract = (
        "## Output\n"
        "When you are done, call the `" + output_tool_name + "` tool exactly once "
        "with these fields as named arguments:\n"
        + "\n".join(lines)
    )
    return (base + "\n\n" + contract) if base else contract


def _staged_output(ctx: AgentContext, staged_holder: dict[str, Any]) -> Optional[dict]:
    staged = getattr(ctx, "staged_subagent_output", None)
    if staged is not None:
        return staged
    if isinstance(staged_holder.get("output"), dict):
        return staged_holder["output"]
    return None


def _make_set_output_tool(output_fields: dict, ctx: AgentContext, staged_holder: dict[str, Any]) -> StructuredTool:
    field_list = ", ".join(output_fields)
    schema_fields: dict[str, Any] = {}
    for name, spec in output_fields.items():
        spec = spec if isinstance(spec, dict) else {}
        py_type = _TYPE_MAP.get((spec.get("type") or "string").lower(), str)
        if py_type is str:
            default: Any = ""
        elif py_type is bool:
            default = False
        else:
            default = 0
        schema_fields[name] = (
            Optional[py_type],
            Field(default=default, description=spec.get("description", "")),
        )
    ArgsSchema = create_model("SetOutputArgs", **schema_fields)

    async def set_output(**kwargs) -> str:
        """Record the final structured result and immediately end the subagent run."""
        coerced = coerce_to_fields(kwargs, output_fields)
        staged_holder["output"] = coerced
        ctx.staged_subagent_output = coerced
        return f"Output recorded ({field_list})."

    return StructuredTool.from_function(
        coroutine=set_output,
        name="set_output",
        description=(
            "Record your final result and immediately end the run. "
            f"Pass these fields as named arguments: {field_list}. For large "
            "artifacts, write the artifact to a file first and put the file "
            "path in the appropriate output field."
        ),
        args_schema=ArgsSchema,
        return_direct=True,
    )


def _subagent_context_middleware(
    ctx: AgentContext,
    request_tool_approval: Callable[[str, str, dict], Awaitable[str]] | None = None,
) -> list:
    """Return compaction plus an optional Plan-owned approval gate.

    Ordinary inline/background Subagents omit the callback and stay unattended.
    Dynamic Execution Plan workers pass it so dangerous file/shell calls suspend
    on the node-owned durable HITL request before execution.
    """
    agent_cfg = getattr(ctx, "agent_cfg", None)
    try:
        if isinstance(agent_cfg, dict):
            model = str(agent_cfg.get("model") or "")
            context_tokens = agent_cfg.get("model_context_tokens")
            model_retries = agent_cfg.get("model_retries", 2)
        else:
            model = getattr(agent_cfg, "model", "") if agent_cfg is not None else ""
            context_tokens = getattr(agent_cfg, "model_context_tokens", None)
            model_retries = getattr(agent_cfg, "model_retries", 2)
        context_tokens = int(context_tokens) if context_tokens else None
    except Exception:
        model = ""
        context_tokens = None
        model_retries = 2
    trigger = int(context_tokens * 0.50) if context_tokens else 80_000
    clear_at_least = int(context_tokens * 0.10) if context_tokens else 20_000
    middleware: list = [
        # A failed model call has no committed tool side effect, so retrying
        # transient 429/5xx provider failures is safe. Tool retries remain
        # conservative: only the known read-only set can retry.
        RuntimeResilienceMiddleware(
            max_model_calls=32,
            max_tool_calls=64,
            wall_clock_s=900,
            model_retries=model_retries,
            read_tool_retries=1,
        ),
        ContextEditingMiddleware(edits=[
            LifecyclePolicyEdit(
                trigger=trigger,
                clear_at_least=clear_at_least,
                model=model,
                s2a=None,
                s2b=None,
                vfs_reader=None,
            )
        ])
    ]
    if request_tool_approval is not None:
        middleware.append(PlanSubagentApprovalMiddleware(request_tool_approval))
    return middleware


async def run_bounded_agent(
    *,
    model,
    tools,
    system_prompt: str,
    user_input: str,
    output_fields: dict,
    max_iterations: int = 25,
    context: Optional[AgentContext] = None,
    checkpointer: Any = None,
    thread_id: Optional[str] = None,
    on_trace: Callable[[dict], Awaitable[None]] | None = None,
    request_tool_approval: Callable[[str, str, dict], Awaitable[str]] | None = None,
) -> SubAgentResult:
    """Run a bounded ReAct agent to a structured result via the terminal tool.

    ``checkpointer`` + ``thread_id``: when both are supplied the full message
    trajectory is persisted to the checkpointer under that thread ID (e.g.
    ``sub:{chat_id}:{uuid}``).  With ``checkpointer=None`` (default) the agent
    runs statelessly — nothing is persisted beyond the returned ``trace``.
    """
    ctx = context or AgentContext()

    # The agent-as-tool reuses the MAIN agent's ctx across run_subagent calls, so
    # clear any output staged by a PRIOR sub-run before this one starts — else a
    # later run that never calls set_output would wrongly surface stale output as
    # "done". Reset BEFORE the stop_event short-circuit (no run has happened yet).
    ctx.staged_subagent_output = None

    # Run-level cancellation: the workflow seeds ``extra['stop_event']`` into the
    # SubAgentNode's AgentContext as ``ctx.stop_event``. If it is already set when
    # the SubAgent would run, short-circuit to a cancelled result WITHOUT invoking
    # the model — matching the engine's ``stop_event is not None and
    # stop_event.is_set()`` convention (e.g. engine/nodes/trigger.py).
    # NOTE: workflow-surface interaction hooks (request_human) are INERT in
    # HITL is the interactive adapter; non-interactive calls skip it.
    stop_event = getattr(ctx, "stop_event", None)
    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
        return SubAgentResult(
            "error",
            coerce_to_fields({}, output_fields),
            trace=[],
            error="cancelled",
        )

    staged_holder: dict[str, Any] = {}
    set_output_tool = _make_set_output_tool(output_fields, ctx, staged_holder)

    agent = create_agent(
        model=model,
        tools=[*tools, set_output_tool],
        context_schema=AgentContext,
        checkpointer=checkpointer,
        middleware=_subagent_context_middleware(ctx, request_tool_approval),
    )

    messages = [
        {"role": "system", "content": _compose_system_message(
            system_prompt, output_fields, set_output_tool.name
        )},
        {"role": "user", "content": user_input},
    ]

    # thread_id is required by a checkpointer; omit configurable entirely when
    # running statelessly so LangGraph doesn't error on a missing thread_id.
    invoke_config: dict = {"recursion_limit": max_iterations * 2 + 1}
    if checkpointer is not None and thread_id:
        invoke_config["configurable"] = {"thread_id": thread_id}

    try:
        if on_trace is None:
            result = await agent.ainvoke(
                {"messages": messages},
                context=ctx,
                config=invoke_config,
            )
        else:
            # Background Plan nodes publish complete public message/tool
            # activities as they settle. This is deliberately message-grained,
            # not private reasoning or a raw token firehose.
            result = {"messages": []}
            published = 0
            async for state in agent.astream(
                {"messages": messages},
                context=ctx,
                config=invoke_config,
                stream_mode="values",
            ):
                if not isinstance(state, dict):
                    continue
                result = state
                current_messages = list(state.get("messages") or [])
                trace = _messages_to_trace(current_messages)
                for entry in trace[published:]:
                    await on_trace(entry)
                published = len(trace)
    except GraphRecursionError:
        # Bounded budget reached without the terminal tool firing — that's an
        # INCOMPLETE result (the agent ran out of rounds), not a crash. Prefer
        # any output the agent DID stage before overrunning.
        staged = _staged_output(ctx, staged_holder)
        return SubAgentResult(
            "incomplete",
            coerce_to_fields(staged or {}, output_fields),
            trace=[],
            error="max_iterations reached",
        )
    except Exception as exc:  # model error — never raise out
        staged = _staged_output(ctx, staged_holder)
        if staged is not None:
            return SubAgentResult(
                "done", coerce_to_fields(staged, output_fields), trace=[]
            )
        return SubAgentResult(
            "error",
            coerce_to_fields({}, output_fields),
            trace=[],
            error=f"{type(exc).__name__}: {exc}",
        )

    trace = _messages_to_trace(result.get("messages", []))
    staged = _staged_output(ctx, staged_holder)
    if staged is not None:
        return SubAgentResult(
            "done", coerce_to_fields(staged, output_fields), trace=trace
        )
    return SubAgentResult(
        "incomplete", coerce_to_fields({}, output_fields), trace=trace
    )

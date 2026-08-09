"""subagent tool — delegate a bounded, closed sub-task to an isolated worker agent.

Design principles:

  1. **Partial tool set**: the worker gets fs + data + media + web + sandbox only.
     No state/todo tools, no build/canvas tools, no browser,
     no MCP, no skill.  The task must be solvable with these tools alone.

  2. **No MCP or skill**: the subagent is a closed executor, not a planner.
     It cannot discover or load external integrations at runtime.

  3. **Explicit output contract**: the worker MUST call ``set_output`` to return
     its result to the main agent.  For large artifacts (code, reports, datasets)
     the artifact pattern applies — write the content to a file first, then put
     the file path in ``set_output``.  Only short results belong inline.

  4. **Checkpointed trajectory**: each invocation is assigned a thread ID
     (``sub:{chat_id}:{tool_call_id}``) and written to the app-level checkpointer
     so the full message trajectory can be exported and replayed for debugging.

Architecture:
  * Creates a fresh worker ``AgentContext`` with the parent's runtime boundary.
    so ``staged_subagent_output`` never bleeds back to the main agent.
  * Builds the model from ``ctx.agent_cfg`` at call time (no build-time binding).
  * Calls ``run_bounded_agent`` with the app checkpointer + generated thread ID.
"""
from __future__ import annotations

from typing import Annotated

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from pydantic import Field

from vibecanvas_api.agents.tools.decorator import ToolError, tool_output
from vibecanvas_api.agents.tools.render import Rendered, register_render

_DEFAULT_SYSTEM_PROMPT = (
    "You are a focused worker sub-agent tasked with ONE bounded, closed problem. "
    "Complete it independently, then call set_output exactly once to return your result. "
    "You receive no parent conversation history: the delegated task packet is your "
    "complete source of background, requirements, constraints, and prior decisions. "
    "Do not assume missing context from the parent conversation. Follow the task "
    "packet's action mode exactly: read-only work must not edit files. If essential "
    "information is missing, do all safe in-scope work first, then return the precise "
    "blocker instead of inventing requirements.\n\n"
    "Tools available:\n"
    "  - fs: read_file, write_file, edit_file, grep\n"
    "  - media: read_images\n"
    "  - web: web_search\n"
    "  - sandbox: bash (shell / code execution; use short Python scripts and "
    "openpyxl for tabular files)\n\n"
    "Output protocol:\n"
    "  - Short result (< ~2 000 tokens): include the content directly in set_output.\n"
    "  - Large artifact (report, code file, dataset): write it to a file using write_file "
    "or bash first, then put the file path in set_output so the main agent can read it "
    "with read_file.\n"
    "  - Make the result self-contained for the calling agent: state the outcome, "
    "important evidence or changed paths, verification performed, and any blocker.\n\n"
    "You do NOT have access to: todo, workflow/canvas tools, browser tools, MCP, "
    "or skill tools. Stay strictly within the given task."
)

_OUTPUT_FIELDS = {"result": {"type": "string", "description": "concise result or file path"}}


def _make_thread_id(ctx, tool_call_id: str) -> str:
    """Build the checkpoint thread ID for this subagent invocation.

    Format: ``sub:{parent_id}:{tool_call_id}``
      - ``parent_id``: chat_id (interactive) → run_id (workflow engine) → "anon"
      - ``tool_call_id``: the main agent's tool call ID — unique per invocation
        and creates a direct 1:1 link between the parent's tool_calls list and
        the subagent checkpoint.

    Query pattern:
        ``SELECT * FROM checkpoints WHERE thread_id LIKE 'sub:{chat_id}:%'``
    """
    parent_id = ctx.chat_id or ctx.run_id or "anon"
    return f"sub:{parent_id}:{tool_call_id}"


# ---------------------------------------------------------------------------
# Three-layer pattern
# ---------------------------------------------------------------------------

@register_render("subagent")
def _render_subagent(raw: dict, ctx) -> Rendered:
    title = str(raw.get("title") or "").strip()
    status = raw.get("status", "unknown")
    result_text = raw.get("result", "")
    error = raw.get("error", "")
    thread_id = raw.get("thread_id", "")
    job_id = raw.get("job_id", "")
    first_line = next(
        (ln.strip() for ln in result_text.splitlines() if ln.strip()), ""
    )
    label = title[:80] or first_line[:80]
    if status == "done" and label:
        abstract = f"subagent → done: {label}"
    elif status == "incomplete":
        abstract = f"subagent → incomplete ({error or 'no output'})"
    elif status == "error":
        abstract = f"subagent → error: {error or 'unknown'}"
    elif status == "queued" and job_id:
        abstract = f"subagent → queued in background [{job_id}]"
    else:
        abstract = f"subagent → {status}"
    if thread_id:
        abstract += f" [thread:{thread_id}]"
    content = (
        f"Background subagent accepted.\njob_id: {job_id}\nstatus: queued"
        if status == "queued" and job_id
        else result_text or error or "(no output)"
    )
    return Rendered(
        content=content,
        content_type="text/plain",
        abstract=abstract,
        extras={
            key: value
            for key, value in {
                "thread_id": thread_id,
                "job_id": job_id,
            }.items()
            if value
        } or None,
    )


@tool_output(content_type="text/plain", tool="subagent")
async def _do_subagent(
    title: str,
    prompt: str,
    max_iterations: int,
    runtime: ToolRuntime,
    background_run: bool = False,
) -> dict:
    from vibecanvas_api import context as app_ctx
    from vibecanvas_api.agent import AgentContext, _build_chat_model
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent
    from vibecanvas_api.agents.tools.subagent.toolset import build_agent_subagent_tools
    from vibecanvas_api.config import config

    title_clean = (title or "").strip()
    prompt_clean = (prompt or "").strip()
    if not title_clean:
        raise ToolError(
            "invalid_subagent_input",
            "Subagent title is required. Provide one concise sentence describing the delegated job.",
            info={"field": "title"},
        )
    if "\n" in title_clean or len(title_clean) > 160:
        raise ToolError(
            "invalid_subagent_input",
            "Subagent title must be a single concise line no longer than 160 characters.",
            info={"field": "title", "max_chars": 160},
        )
    if not prompt_clean:
        raise ToolError(
            "invalid_subagent_input",
            "Subagent prompt is required and must contain the complete, self-contained task packet.",
            info={"field": "prompt"},
        )
    if not 1 <= max_iterations <= 100:
        raise ToolError(
            "invalid_subagent_input",
            "max_iterations must be between 1 and 100.",
            info={"field": "max_iterations", "minimum": 1, "maximum": 100},
        )

    ctx = runtime.context
    if background_run:
        submitter = getattr(ctx, "background_job_submitter", None)
        if not callable(submitter):
            raise ToolError(
                "background_job_unavailable",
                "Background subagents require the LangChain sandbox Runtime control plane.",
            )
        response = await submitter(
            runtime.tool_call_id,
            {
                "executor_type": "langchain_subagent",
                "tool_name": "subagent",
                "title": title_clean,
                "input": {
                    "title": title_clean,
                    "prompt": prompt_clean,
                    "max_iterations": max_iterations,
                },
            },
        )
        if response.get("action") != "accepted":
            raise ToolError(
                "background_job_rejected",
                str(response.get("error") or "the background job was rejected"),
            )
        return {
            "title": title_clean,
            "status": "queued",
            "result": "",
            "error": "",
            "thread_id": "",
            "job_id": str(response.get("job_id") or ""),
            "background_run": True,
        }

    # Fresh isolated worker context — staged_subagent_output on the worker
    # must never overwrite the main agent's field.
    worker_ctx = AgentContext(
        workflow=ctx.workflow,
        repo=ctx.repo,
        vfs=ctx.vfs,
        vfs_run=ctx.vfs_run,
        username=ctx.username,
        wf_id=ctx.wf_id,
        chat_id=ctx.chat_id,
        run_id=ctx.run_id,
        tenant_id=ctx.tenant_id,
        agent_cfg=ctx.agent_cfg,
    )

    agent_cfg = ctx.agent_cfg or config.agent
    try:
        model = _build_chat_model(agent_cfg)
    except Exception as exc:
        raise ToolError("model_build_failed", f"could not build subagent model: {exc}")

    tools = build_agent_subagent_tools()
    thread_id = _make_thread_id(ctx, runtime.tool_call_id)

    result = await run_bounded_agent(
        model=model,
        tools=tools,
        system_prompt=_DEFAULT_SYSTEM_PROMPT,
        user_input=(
            "# Delegated task\n"
            f"Title: {title_clean}\n\n"
            "## Complete task packet\n"
            f"{prompt_clean}"
        ),
        output_fields=_OUTPUT_FIELDS,
        max_iterations=max_iterations,
        context=worker_ctx,
        checkpointer=app_ctx.checkpointer,  # may be None in tests / dev
        thread_id=thread_id,
    )

    return {
        "title": title_clean,
        "status": result.status,
        "result": result.output.get("result", ""),
        "error": result.error or "",
        "thread_id": thread_id,
    }


@tool(response_format="content_and_artifact")
async def subagent(
    title: Annotated[
        str,
        Field(
            description=(
                "One concise sentence naming the delegated job. This is a label, "
                "not the task instructions or context."
            )
        ),
    ],
    prompt: Annotated[
        str,
        Field(
            description=(
                "A detailed, self-contained task brief the worker can execute once and "
                "autonomously. It receives no parent history and returns one final "
                "result. State the action mode (research/analyze, modify, or create), "
                "all required context and exact inputs, scope and non-goals, the exact "
                "result to return, and how completion must be verified. Never refer to "
                "'the discussion above' or other context that is not included here."
            )
        ),
    ],
    max_iterations: Annotated[
        int,
        Field(
            description=(
                "Maximum worker model/tool rounds. Use the default for normal bounded "
                "work; increase only for a clearly larger independent task."
            )
        ),
    ] = 25,
    background_run: Annotated[
        bool,
        Field(
            description=(
                "Run this delegated task as a durable background job. Use true "
                "only when the parent can continue useful independent work and "
                "let the platform deliver its durable result in a later control "
                "Turn after the foreground Turn is idle. "
                "Defaults to false for synchronous delegation."
            )
        ),
    ] = False,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delegate one bounded, independent task to an isolated worker agent.

    Use this for a complex, multi-step, or context-heavy task that can be
    completed independently and summarized back to the main Agent. Perform a
    simple task directly when it only needs one or two obvious tool calls. Do not
    delegate work that depends on browser state, canvas control, MCP servers,
    skills, user interaction, or unstated details from this conversation.

    The worker starts with a fresh context. It does NOT receive the parent chat,
    earlier messages, hidden reasoning, prior tool results, or an implicit summary.
    The ``prompt`` argument is the only task context transferred from the parent.
    Each invocation is one-shot: the parent receives the final result, not the
    worker's intermediate reasoning or tool results, and cannot clarify the task
    mid-run. Therefore the prompt must stand alone even when referenced files are
    available in the shared filesystem.

    A strong ``prompt`` normally states:
      - objective and action mode: research/analyze only, modify, or create;
      - concrete inputs: paths, facts, errors, and relevant prior decisions;
      - scope, authority, constraints, non-goals, and behavior to preserve;
      - the exact final result to return, including evidence or artifact paths;
      - verification steps and acceptance criteria.

    Avoid dangling references such as "use the file mentioned above", "continue
    the earlier fix", or "follow our previous decision". Copy the necessary file
    path, decision, error, or requirement into ``prompt`` itself.

    The worker runs its own tool-use loop with this curated capability subset:

      fs      — file read / write / edit / search
      media   — image reading
      web     — web search
      sandbox — shell and code execution, including tabular work through
                Python/openpyxl

    The worker shares the main agent's file storage, so files it writes are
    immediately readable by the main agent.

    Output contract — the worker MUST call set_output to return its result:
      - Short result: the content goes directly into set_output.
      - Large artifact (code, report, data): the worker writes it to a file and
        puts the file path in set_output; the main agent reads it via read_file.

    ``title`` is only a short routing/trace label. Never put essential context
    only in the title. ``prompt`` contains the actual assignment.

    Status:
        done       — worker called set_output; result field is populated.
        incomplete — worker exhausted its round budget without calling set_output.
        error      — model error or external cancellation.

    Good examples:
        # Repository task with explicit scope, context, and acceptance checks.
        subagent(
            title="Audit refresh-token validation in the API",
            prompt=(
                "Background: users occasionally enter a login loop after a refresh. "
                "Inspect api/src/vibecanvas_api/auth/refresh.py and its direct tests. "
                "Determine whether token rotation can accept a previously revoked "
                "token. Do not change code or unrelated auth behavior. Return the "
                "execution path with file/line references, root cause, smallest "
                "recommended fix, and verification evidence."
            ),
        )

        # File-producing task with an explicit destination and verification.
        subagent(
            title="Add focused tests for CSV delimiter detection",
            prompt=(
                "Work only in api/tests/data/test_csv_detection.py. The parser is "
                "api/src/vibecanvas_api/data/csv_detection.py. Add tests for UTF-8 "
                "BOM, tab delimiters, and quoted commas without modifying the parser. "
                "Follow existing pytest style, run that test file, and return the "
                "edited path plus exact result. Do not alter shared fixtures."
            ),
        )

    Bad example: ``title="Review auth", prompt="Check the auth code."`` It omits
    paths, the suspected behavior, scope, deliverable, and acceptance criteria,
    forcing the worker to guess context it cannot access.
    """
    return await _do_subagent(
        title,
        prompt,
        max_iterations,
        runtime,
        background_run,
    )

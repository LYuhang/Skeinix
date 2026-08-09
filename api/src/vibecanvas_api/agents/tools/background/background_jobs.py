"""Query and cancel durable LangChain background jobs through the host."""

from __future__ import annotations

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools.decorator import ToolError, tool_output
from vibecanvas_api.agents.tools.render import Rendered, register_render


def _controller(runtime: ToolRuntime):
    callback = getattr(runtime.context, "background_job_submitter", None)
    if not callable(callback):
        raise ToolError(
            "background_job_unavailable",
            "Background job controls require the LangChain sandbox Runtime.",
        )
    return callback


@register_render("background_job_list")
def _render_list(raw: dict, ctx) -> Rendered:
    jobs = list(raw.get("jobs") or [])
    lines = [f"Background jobs: {len(jobs)}"]
    for job in jobs:
        progress = job.get("progress") or {}
        progress_text = str(progress.get("message") or "").strip()
        suffix = f" — {progress_text}" if progress_text else ""
        lines.append(
            f"- {job.get('job_id')}: {job.get('title') or '(untitled)'} "
            f"[{job.get('status') or 'unknown'}]{suffix}"
        )
    if not jobs:
        lines.append("(none)")
    return Rendered(
        content="\n".join(lines),
        content_type="text/plain",
        abstract=f"background_job_list → {len(jobs)} job(s)",
    )


@tool_output(content_type="text/plain", tool="background_job_list")
async def _do_background_job_list(
    include_finished: bool,
    limit: int,
    cursor: str | None,
    runtime: ToolRuntime,
) -> dict:
    response = await _controller(runtime)(
        runtime.tool_call_id,
        {
            "operation": "list",
            "include_finished": bool(include_finished),
            "limit": max(1, min(int(limit), 100)),
            "cursor": (cursor or "").strip() or None,
        },
    )
    if response.get("action") != "accepted":
        raise ToolError(
            "background_job_list_failed",
            str(response.get("error") or "the backend rejected the list request"),
        )
    payload = response.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    jobs = payload.get("jobs")
    return {
        "jobs": jobs if isinstance(jobs, list) else [],
        "next_cursor": payload.get("next_cursor"),
    }


@tool(response_format="content_and_artifact")
async def background_job_list(
    include_finished: bool = False,
    limit: int = 50,
    cursor: str | None = None,
    *,
    runtime: ToolRuntime,
) -> str:
    """List this Chat's durable LangChain Subagent jobs.

    By default returns only queued, running, and cancelling jobs. Set
    ``include_finished=true`` only when recent terminal status is needed.
    Terminal results are delivered automatically, so do not poll this tool or
    use it as a replacement for result delivery.
    """
    return await _do_background_job_list(include_finished, limit, cursor, runtime)


@register_render("background_job_get")
def _render_get(raw: dict, ctx) -> Rendered:
    job = raw.get("job") if isinstance(raw.get("job"), dict) else {}
    job_id = str(job.get("job_id") or raw.get("job_id") or "")
    title = str(job.get("title") or "(untitled)")
    status = str(job.get("status") or "unknown")
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    progress_message = str(progress.get("message") or "").strip()
    current = progress.get("current")
    total = progress.get("total")
    lines = [f"Background job {job_id}: {title}", f"Status: {status}"]
    if total is not None:
        lines.append(f"Progress: {current or 0}/{total}")
    elif current:
        lines.append(f"Progress: {current}")
    if progress_message:
        lines.append(f"Current activity: {progress_message}")

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    result_ref = str(job.get("result_ref") or "").strip()
    error = job.get("error") if isinstance(job.get("error"), dict) else {}
    if result:
        lines.extend(
            ["Result:", json.dumps(result, ensure_ascii=False, indent=2, default=str)]
        )
    if result_ref:
        lines.append(f"Full result: {result_ref}")
    if error:
        lines.extend(
            ["Error:", json.dumps(error, ensure_ascii=False, indent=2, default=str)]
        )
    if not result and not result_ref and not error:
        lines.append("Result: not available yet")
    return Rendered(
        content="\n".join(lines),
        content_type="text/plain",
        abstract=f"background_job_get → {job_id} [{status}]",
        extras={"job_id": job_id, "status": status},
    )


@tool_output(content_type="text/plain", tool="background_job_get")
async def _do_background_job_get(
    job_id: str,
    runtime: ToolRuntime,
) -> dict:
    clean_job_id = (job_id or "").strip()
    if not clean_job_id:
        raise ToolError("invalid_background_job", "job_id is required")
    response = await _controller(runtime)(
        runtime.tool_call_id,
        {"operation": "get", "job_id": clean_job_id},
    )
    if response.get("action") != "accepted":
        raise ToolError(
            "background_job_get_failed",
            str(response.get("error") or "the backend rejected the status request"),
        )
    payload = response.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    job = payload.get("job")
    if not isinstance(job, dict):
        raise ToolError(
            "background_job_get_failed",
            "the backend returned an invalid background job response",
        )
    return {"job_id": clean_job_id, "job": job}


@tool(response_format="content_and_artifact")
async def background_job_get(
    job_id: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Get one durable background job's current status and available result.

    Use when the user asks about a submitted job or when its output is needed
    to continue the current task. The response includes progress, terminal
    errors, an inline result when available, and any durable full-result ref.
    Do not poll repeatedly; terminal results are also delivered automatically.
    """
    return await _do_background_job_get(job_id, runtime)


@register_render("background_job_cancel")
def _render_cancel(raw: dict, ctx) -> Rendered:
    job = raw.get("job") if isinstance(raw.get("job"), dict) else {}
    job_id = str(job.get("job_id") or raw.get("job_id") or "")
    status = str(job.get("status") or "unknown")
    return Rendered(
        content=f"Background job {job_id} cancellation requested; status: {status}.",
        content_type="text/plain",
        abstract=f"background_job_cancel → {job_id} [{status}]",
        extras={"job_id": job_id},
    )


@tool_output(content_type="text/plain", tool="background_job_cancel")
async def _do_background_job_cancel(
    job_id: str,
    runtime: ToolRuntime,
) -> dict:
    clean_job_id = (job_id or "").strip()
    if not clean_job_id:
        raise ToolError("invalid_background_job", "job_id is required")
    response = await _controller(runtime)(
        runtime.tool_call_id,
        {"operation": "cancel", "job_id": clean_job_id},
    )
    if response.get("action") != "accepted":
        raise ToolError(
            "background_job_cancel_failed",
            str(response.get("error") or "the backend rejected cancellation"),
        )
    payload = response.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    job = payload.get("job")
    return {
        "job_id": clean_job_id,
        "job": job if isinstance(job, dict) else {"job_id": clean_job_id},
    }


@tool(response_format="content_and_artifact")
async def background_job_cancel(
    job_id: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Cancel one durable LangChain Subagent job.

    Use only when the user explicitly asks to stop that job or the current task
    packet makes it unambiguously obsolete. Cancellation is asynchronous for a
    running worker; ``cancelling`` later becomes the terminal ``cancelled``
    state and is delivered through the normal background result path.
    """
    return await _do_background_job_cancel(job_id, runtime)

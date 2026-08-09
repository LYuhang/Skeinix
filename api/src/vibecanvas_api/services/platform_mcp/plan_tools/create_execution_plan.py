"""Validate one plan source file and submit its immutable background run."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools.decorator import ToolError, tool_output
from vibecanvas_api.agents.tools.render import Rendered, register_render
from vibecanvas_api.services.execution_plans.validator import validate_plan_bytes
from vibecanvas_api.services.sandbox.manager import get_existing_sandbox_manager
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.execution_plan_repo import ExecutionPlanRepo


@register_render("create_execution_plan")
def _render_create_execution_plan(raw: dict[str, Any], _ctx) -> Rendered:
    if raw.get("status") == "invalid":
        errors = list(raw.get("errors") or [])
        return Rendered(
            content={
                "status": "invalid",
                "plan_path": raw.get("plan_path"),
                "errors": errors,
                "truncated": bool(raw.get("truncated")),
            },
            content_type="application/json",
            abstract=f"Plan validation failed with {len(errors)} issue(s)",
        )
    product = dict(raw.get("product") or {})
    job_id = str(raw.get("job_id") or "")
    return Rendered(
        # The model needs only the opaque Background Job handle. Product-only
        # identifiers remain in artifact handles for Chat/Preview projection.
        content={"job_id": job_id},
        content_type="application/json",
        abstract=f"Execution plan submitted as {job_id}",
        extras={"execution_plan": product},
    )


def _invocation_id(ctx: Any, *, plan_path: str, source_hash: str) -> str:
    raw = "\0".join(
        (
            "execution-plan-create-v1",
            str(ctx.chat_id),
            str(ctx.turn_id),
            plan_path,
            source_hash,
        )
    ).encode("utf-8")
    return "plan_create_" + hashlib.sha256(raw).hexdigest()


def _authorization_snapshot_hash(ctx: Any) -> str:
    snapshot = {
        "authorization_generation": str(ctx.authorization_generation),
        "membership_id": str(ctx.authorization_membership_id),
        "membership_role": str(ctx.authorization_membership_role),
        "session_generation": int(ctx.authorization_session_generation),
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _flush_loaded_workspace(ctx: Any) -> None:
    """Make in-Turn file edits visible to the durable Plan reader.

    Agent filesystem tools execute inside the already-running sandbox and the
    ordinary bulk writeback happens at the Turn boundary.  A Plan is authored
    and submitted in the same Turn, so the Platform MCP must first flush the
    *already loaded* workspace.  Never create a sandbox here: when no local
    owner exists (for example on another worker), the durable VFS remains the
    only source and the normal not-found result is preserved.
    """
    manager = get_existing_sandbox_manager()
    if manager is None:
        return
    loaded = await manager.get_loaded_session(
        str(ctx.tenant_id),
        str(ctx.wf_id),
    )
    if loaded is not None:
        await loaded.writeback_vfs()


@tool_output(content_type="application/json", tool="create_execution_plan")
async def _do_create_execution_plan(
    plan_path: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    ctx = runtime.context
    if getattr(ctx, "runtime_location", "") != "platform_mcp":
        raise ToolError("invalid_runtime", "create_execution_plan is a Platform MCP tool")
    if not getattr(ctx, "tenant_id", None) or not getattr(ctx, "chat_id", None):
        raise ToolError("missing_context", "the authenticated plan context is incomplete")

    path_probe = validate_plan_bytes(plan_path, b"")
    if any(issue.code == "invalid_plan_path" for issue in path_probe.errors):
        return path_probe.model_dump(mode="json", exclude={"definition"})

    # VFS is the durable authoring source shared by every API worker. Flush the
    # locally owned active workspace first because authoring and submission are
    # intentionally allowed in one Agent Turn.
    try:
        await _flush_loaded_workspace(ctx)
        raw = await asyncio.to_thread(
            ctx.vfs.read_bytes,
            wf_id=ctx.wf_id,
            path=plan_path,
        )
    except Exception as exc:  # noqa: BLE001 - return a bounded model repair hint
        raise ToolError(
            "plan_read_failed",
            f"Could not read {plan_path!r} from the current Chat workspace.",
            info={"reason": type(exc).__name__},
        ) from exc
    if raw is None:
        raise ToolError(
            "plan_not_found",
            f"No plan file exists at {plan_path!r}. Write it under /data/plans first.",
        )
    if not isinstance(raw, bytes):
        raise ToolError("plan_read_failed", "the plan source did not resolve to bytes")

    report = validate_plan_bytes(plan_path, raw)
    if report.status != "valid":
        # This branch is deliberately before session_scope: malformed sources
        # cannot create a Plan, revision, Run, HITL, event, or job identifier.
        return report.model_dump(mode="json", exclude={"definition"})

    tool_invocation_id = _invocation_id(
        ctx,
        plan_path=plan_path,
        source_hash=str(report.source_hash),
    )
    try:
        async with session_scope(tenant_id=str(ctx.tenant_id)) as session:
            submission = await ExecutionPlanRepo(session).create_validated(
                tenant_id=str(ctx.tenant_id),
                chat_id=str(ctx.chat_id),
                creator_user_id=str(ctx.username),
                parent_turn_id=str(ctx.turn_id),
                tool_invocation_id=tool_invocation_id,
                approval_mode=str(ctx.approval_mode),
                authorization_snapshot_hash=_authorization_snapshot_hash(ctx),
                report=report,
            )
            await session.commit()
    except ValueError as exc:
        raise ToolError("plan_submission_conflict", str(exc)) from exc

    product = {
        "kind": "execution_plan",
        "plan_id": submission.plan_id,
        "revision": submission.revision,
        "plan_run_id": submission.plan_run_id,
        "job_id": submission.job_id,
        "status": submission.status,
        "preview_resource": (
            f"execution_plan:{submission.plan_id}:{submission.revision}"
        ),
    }
    return {
        "status": "submitted",
        "job_id": submission.job_id,
        "product": product,
    }


@tool(response_format="content_and_artifact")
async def create_execution_plan(plan_path: str, *, runtime: ToolRuntime):
    """Validate and submit one durable static Execution Plan file.

    Write the complete JSON definition to
    ``/data/plans/<name>.plan.json`` first, then pass that exact path here.
    The backend performs strict parsing, DAG, structured fork/join, fixed
    schema, budget, and live-authorization checks before creating anything.
    A split is a node with multiple values in ``next``; all branches must
    converge at one shared merge and parallel regions may nest but not cross.
    If validation fails, repair the file using the returned structured issues
    and call this tool again.

    A valid call returns only an opaque ``job_id``. Use the LangChain-private
    background job list/get/cancel tools to inspect or manage it. Depending on
    the current trusted approval mode, execution either queues automatically or
    waits for the user to approve the immutable plan in its Preview.

    Args:
        plan_path: Exact ``/data/plans/*.plan.json`` source path.
    """
    return await _do_create_execution_plan(plan_path, runtime=runtime)

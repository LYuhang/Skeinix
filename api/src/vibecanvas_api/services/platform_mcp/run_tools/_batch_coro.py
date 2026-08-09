# -*- coding: utf-8 -*-
# Platform MCP batch execution coroutine.
"""batch_execute internals: run parsed rows through the shared sandbox runner."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4
from typing import Any

def _jsonl_record(index: int, input_row: dict, rj: dict | None, error: str | None = None) -> dict:
    if rj is None:
        return {
            "index": index,
            "status": "error",
            "input": input_row,
            "output": None,
            "node_outputs": {},
            "errors": {"_row_error": error or "no result"},
            "execution_time": 0.0,
        }
    node_outputs = rj.get("final_outputs") or {}
    errors = {k: str(v) for k, v in (rj.get("error_dict") or {}).items()}
    return {
        "index": index,
        "status": "error" if errors else "success",
        "input": input_row,
        "output": node_outputs.get("__end__"),
        "node_outputs": node_outputs,
        "errors": errors,
        "execution_time": round(rj.get("execution_time", 0.0) or 0.0, 3),
    }


async def _batch_coro_rows(rows: list[dict], output_path: str, workflow: dict, ctx: Any,
                           row_concurrency: int, session) -> dict:
    """Run already-parsed input rows and write one JSON object per line.

    Output JSONL protocol:
    `{index,status,input,output,node_outputs,errors,execution_time}`.
    """
    from vibecanvas_api.agents.tools.decorator import ToolError
    from vibecanvas_api.services.platform_mcp.run_tools._backend import _save_if_dirty

    if not rows:
        await session.write_file(output_path, "")
        await session.writeback_vfs()
        return {"result_path": output_path, "total": 0, "failed_rows": 0,
                "result_summary": f"0 rows — empty result written to {output_path}"}

    save_err = await asyncio.to_thread(_save_if_dirty, ctx)
    if save_err:
        raise ToolError("save_before_run_failed", save_err)

    tenant = getattr(ctx, "tenant_id", None)
    batch_id = uuid4().hex[:8]
    jobs = []
    for i, row in enumerate(rows):
        sub = f"{batch_id}/{i}"
        jobs.append({"kind": "workflow", "tenant": tenant or "",
                     "run_id": f"{batch_id}_{i}", "run_subpath": sub,
                     "inputs": row})

    sem = asyncio.Semaphore(max(1, int(row_concurrency or 1)))

    async def _run_one(job: dict) -> dict:
        async with sem:
            return await session.execute_workflow_job(
                workflow=workflow,
                inputs=job["inputs"],
                extra=None,
                tenant=job.get("tenant") or "",
                run_id=job.get("run_id") or "",
                run_subpath=job.get("run_subpath") or "",
            )

    statuses = await asyncio.gather(*[_run_one(j) for j in jobs])

    failed = 0
    records = []
    for i, (row, outcome) in enumerate(zip(rows, statuses)):
        status = outcome.get("status") or {}
        rec = _jsonl_record(
            i,
            row,
            outcome.get("result"),
            status.get("error_message") if isinstance(status, dict) else None,
        )
        if rec["status"] != "success":
            failed += 1
        records.append(rec)
    text = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in records)
    if text:
        text += "\n"
    res = await session.write_file(output_path, text)
    if not res.get("ok"):
        raise ToolError("write_failed", f"could not write batch output {output_path!r}")
    await session.writeback_vfs()
    return {"result_path": output_path, "total": len(rows), "failed_rows": failed,
            "result_summary": f"{len(rows)} rows done, {failed} errors → {output_path}"}

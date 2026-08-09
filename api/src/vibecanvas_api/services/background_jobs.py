"""Backend dispatcher for durable asynchronous tool jobs."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from typing import Any

import structlog

from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeBackgroundJobRequest,
)
from vibecanvas_api.storage.background_jobs_repo import BackgroundJobsRepo
from vibecanvas_api.storage.db import session_scope


logger = structlog.get_logger(__name__)


def background_job_id(turn_id: str, tool_call_id: str) -> str:
    stable = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"vibecanvas:background-job:{turn_id}:{tool_call_id}",
    )
    return f"job_subagent_{stable.hex[:20]}"


class BackgroundJobDispatcher:
    """Own local executor tasks while PostgreSQL remains the source of truth."""

    def __init__(self) -> None:
        self.owner = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def submit_langchain_subagent(
        self,
        *,
        sandbox,
        tenant_id: str,
        user_id: str,
        chat_id: str,
        parent_turn_id: str,
        runtime_root: str,
        tool_call_id: str,
        job_spec: dict[str, Any],
        model: dict[str, Any],
    ) -> tuple[str, bool]:
        if str(job_spec.get("executor_type") or "") != "langchain_subagent":
            raise ValueError("unsupported background executor")
        if str(job_spec.get("tool_name") or "") != "subagent":
            raise ValueError("langchain_subagent must originate from subagent")
        input_snapshot = job_spec.get("input")
        input_snapshot = (
            dict(input_snapshot) if isinstance(input_snapshot, dict) else {}
        )
        title = str(job_spec.get("title") or input_snapshot.get("title") or "").strip()
        prompt = str(input_snapshot.get("prompt") or "").strip()
        max_iterations = int(input_snapshot.get("max_iterations") or 25)
        if not title or not prompt or not 1 <= max_iterations <= 100:
            raise ValueError("invalid background subagent task packet")

        job_id = background_job_id(parent_turn_id, tool_call_id)
        async with session_scope(tenant_id=tenant_id) as session:
            row, created = await BackgroundJobsRepo(session).create_idempotent(
                job_id=job_id,
                tenant_id=tenant_id,
                chat_id=chat_id,
                creator_user_id=user_id,
                parent_run_id=parent_turn_id,
                runtime_type="langchain",
                executor_type="langchain_subagent",
                tool_name="subagent",
                title=title,
                input_snapshot={
                    "title": title,
                    "prompt": prompt,
                    "max_iterations": max_iterations,
                },
                idempotency_key=f"{parent_turn_id}:{tool_call_id}",
            )
            status = row.status
        if created or status == "queued":
            request = RuntimeBackgroundJobRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                chat_id=chat_id,
                parent_turn_id=parent_turn_id,
                job_id=job_id,
                runtime_root=runtime_root,
                title=title,
                prompt=prompt,
                max_iterations=max_iterations,
                model=dict(model or {}),
            )
            await self.start(
                job_id=job_id,
                tenant_id=tenant_id,
                sandbox=sandbox,
                request=request,
            )
        return job_id, created

    async def start(
        self,
        *,
        job_id: str,
        tenant_id: str,
        sandbox,
        request: RuntimeBackgroundJobRequest,
    ) -> bool:
        async with self._lock:
            current = self._tasks.get(job_id)
            if current is not None and not current.done():
                return False
            task = asyncio.create_task(
                self._run(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    sandbox=sandbox,
                    request=request,
                ),
                name=f"background-job:{job_id}",
            )
            self._tasks[job_id] = task
            task.add_done_callback(
                lambda done, jid=job_id: self._finished(jid, done)
            )
            return True

    def _finished(self, job_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("background_job_dispatch_failed", job_id=job_id)

    async def _run(
        self,
        *,
        job_id: str,
        tenant_id: str,
        sandbox,
        request: RuntimeBackgroundJobRequest,
    ) -> None:
        async with session_scope(tenant_id=tenant_id) as session:
            claimed = await BackgroundJobsRepo(session).claim(
                job_id=job_id,
                owner=self.owner,
                lease_seconds=30,
                execution_handle={
                    "owner": self.owner,
                    "transport": "sandbox_runtime_bus",
                },
            )
        if claimed is None:
            return

        events = sandbox.run_background_job_stream(
            request.model_dump(mode="json")
        )
        next_event: asyncio.Task | None = asyncio.create_task(anext(events))
        cancel_sent = False
        heartbeat_tick = 0
        saw_result = False
        try:
            while next_event is not None:
                poll = asyncio.create_task(asyncio.sleep(1.0))
                done, _ = await asyncio.wait(
                    {next_event, poll},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if next_event in done:
                    try:
                        item = next_event.result()
                    except StopAsyncIteration:
                        next_event = None
                        break
                    kind = str(item.get("kind") or "")
                    if kind == "event":
                        progress = item.get("progress")
                        progress = (
                            progress if isinstance(progress, dict) else {}
                        )
                        async with session_scope(
                            tenant_id=tenant_id
                        ) as session:
                            await BackgroundJobsRepo(session).heartbeat(
                                job_id=job_id,
                                owner=self.owner,
                                lease_seconds=30,
                                current=progress.get("current"),
                                total=progress.get("total"),
                                message=progress.get("message"),
                            )
                    elif kind == "result":
                        saw_result = True
                        status = str(item.get("status") or "error")
                        async with session_scope(
                            tenant_id=tenant_id
                        ) as session:
                            repo = BackgroundJobsRepo(session)
                            if status == "done":
                                result = item.get("result")
                                result = (
                                    result if isinstance(result, dict) else {}
                                )
                                result_value = result.get("result")
                                result_ref = (
                                    result_value
                                    if isinstance(result_value, str)
                                    and result_value.startswith(("/data/", "/mount/"))
                                    else None
                                )
                                await repo.complete(
                                    job_id=job_id,
                                    result={
                                        **result,
                                        "thread_id": item.get("thread_id"),
                                    },
                                    result_ref=result_ref,
                                )
                            elif status == "cancelled":
                                await repo.mark_cancelled(
                                    job_id,
                                    reason="executor_cancelled",
                                )
                            else:
                                error = item.get("error")
                                error = (
                                    error if isinstance(error, dict) else {}
                                )
                                await repo.fail(
                                    job_id=job_id,
                                    error=error or {
                                        "code": "subagent_incomplete",
                                        "message": (
                                            "The background subagent did not "
                                            "produce its required output."
                                        ),
                                    },
                                )
                        next_event = None
                        break
                    next_event = asyncio.create_task(anext(events))
                if poll in done:
                    heartbeat_tick += 1
                    async with session_scope(tenant_id=tenant_id) as session:
                        repo = BackgroundJobsRepo(session)
                        row = await repo.get(job_id)
                        if row is None:
                            raise RuntimeError(
                                f"background job {job_id} disappeared"
                            )
                        if row.status == "cancelling" and not cancel_sent:
                            cancel_sent = await sandbox.cancel_background_job(
                                job_id
                            )
                        if (
                            row.status == "running"
                            and heartbeat_tick % 10 == 0
                        ):
                            await repo.heartbeat(
                                job_id=job_id,
                                owner=self.owner,
                                lease_seconds=30,
                            )
                if not poll.done():
                    poll.cancel()
                await asyncio.gather(poll, return_exceptions=True)
            if not saw_result:
                async with session_scope(tenant_id=tenant_id) as session:
                    row = await BackgroundJobsRepo(session).get(job_id)
                    if row is not None and row.status == "cancelling":
                        await BackgroundJobsRepo(session).mark_cancelled(
                            job_id,
                            reason="executor_stream_closed_after_cancel",
                        )
                    elif row is not None and row.status not in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        await BackgroundJobsRepo(session).fail(
                            job_id=job_id,
                            error={
                                "code": "executor_stream_ended",
                                "message": (
                                    "The background executor ended without a result."
                                ),
                            },
                        )
        except asyncio.CancelledError:
            try:
                await sandbox.cancel_background_job(job_id)
            finally:
                # Graceful API shutdown happens while the product database is
                # still available.  Persist an explicit terminal state now;
                # otherwise the UI would show a phantom running job until its
                # lease expires after the next process starts.
                async with session_scope(tenant_id=tenant_id) as session:
                    repo = BackgroundJobsRepo(session)
                    row = await repo.get(job_id)
                    if row is not None and row.status == "cancelling":
                        await repo.mark_cancelled(
                            job_id,
                            reason="executor_stopped_after_cancel",
                        )
                    elif row is not None and row.status not in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        await repo.fail(
                            job_id=job_id,
                            error={
                                "code": "executor_shutdown",
                                "message": (
                                    "The background executor stopped because "
                                    "its API worker shut down."
                                ),
                            },
                        )
                raise
        except Exception as exc:
            async with session_scope(tenant_id=tenant_id) as session:
                await BackgroundJobsRepo(session).fail(
                    job_id=job_id,
                    error={
                        "code": "background_executor_failed",
                        "message": str(exc),
                    },
                )
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
            await events.aclose()
            # Result delivery observes the durable terminal row/event. The
            # executor deliberately does not call into delivery internals.

    async def cancel_local(self, job_id: str, sandbox) -> bool:
        return await sandbox.cancel_background_job(job_id)

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


background_job_dispatcher = BackgroundJobDispatcher()

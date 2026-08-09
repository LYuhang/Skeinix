# -*- coding: utf-8 -*-
"""Own the temporary filesystem lifecycle for one workflow run."""
from __future__ import annotations

import asyncio

import structlog

from vibecanvas_api.services.vfs_run_context import (
    build_run_context,
    release_run,
    sync_run_back,
    sync_run_back_sync,
)
from vibecanvas_api.services.user_mount_workspace import (
    create_user_mount,
    create_user_mount_sync,
    persist_user_mount,
    persist_user_mount_sync,
    remove_user_mount,
)
from vibecanvas_api.storage.vfs_run_repo import PostgresVfsRunStore

logger = structlog.get_logger(__name__)


class RunWorkspace:
    """State-owner for one run's filesystem lifecycle. Delegates to the proven
    lifecycle functions; see module docstring for the C1 contract."""

    def __init__(
        self,
        run_id: str,
        tenant_id: str,
        *,
        wf_id: str | None = None,
        user_id: str | None = None,
        retain: bool = False,
        keep_run: bool = False,
    ) -> None:
        self.run_id = run_id
        self.tenant_id = tenant_id
        self.wf_id = wf_id
        self.user_id = user_id
        self.retain = retain
        # UX-10e0 "keep latest run per workflow": the NORMAL run path persists
        # its /run outputs at release (so the just-finished run is reviewable +
        # renderable) and does NOT delete the run-tier rows/blobs — the PRIOR run
        # of the same workflow is purged at the NEXT run's START
        # (executions.start_execution → VfsRunRepo.purge_workflow_runs). ``retain``
        # (debug-execute) also keeps run files. Default OFF (transient — back-compat for the production online-
        # invoke + batch paths, which must NOT accumulate /run); the canvas
        # builder RUN path (executions._produce_execution) sets keep_run=True so
        # the just-finished run is reviewable + renderable. A wf_id is required
        # for the purge to bound storage; a /run-only run with wf_id=None would
        # keep its run rows but never be purged (NULL wf_id).
        self.keep_run = keep_run
        # populated by prepare:
        self.run_dir: str | None = None
        self.mount_dir: str | None = None

    @property
    def run_context(self) -> dict:
        """What ``wf.trigger`` / the drain consume. Computed fresh each access so
        it always reflects the current run directory."""
        return {
            "run_id": self.run_id,
            "run_dir": self.run_dir,
        }

    # --- granular (delegate) ------------------------------------------------ #
    async def _prepare_async(self) -> None:
        """ASYNC prepare: ``build_run_context`` does blocking DB+ObjectStore+FS
        work, so it MUST run off the event loop (``asyncio.to_thread``)."""
        ctx = await asyncio.to_thread(
            build_run_context, self.run_id, self.tenant_id)
        self.run_dir = ctx["run_dir"]
        if self.user_id:
            self.mount_dir = await create_user_mount(
                user_id=self.user_id, tenant_id=self.tenant_id
            )

    def _prepare_sync(self) -> None:
        """SYNC prepare: genuinely-sync frame (no running loop) → call
        ``build_run_context`` directly, NO to_thread / NO asyncio.run wrapper."""
        ctx = build_run_context(self.run_id, self.tenant_id)
        self.run_dir = ctx["run_dir"]
        if self.user_id:
            self.mount_dir = create_user_mount_sync(
                user_id=self.user_id, tenant_id=self.tenant_id
            )

    async def sync_mount(self) -> int:
        if not self.user_id or not self.mount_dir:
            return 0
        return await persist_user_mount(
            source=self.mount_dir,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )

    def sync_mount_sync(self) -> int:
        if not self.user_id or not self.mount_dir:
            return 0
        return persist_user_mount_sync(
            source=self.mount_dir,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )

    async def sync_run(self) -> int:
        """ASYNC write-back of the run-tier ``/run`` files into ``vfs_run`` so the
        WORKFLOW_SANDBOX Explorer + the raw-media endpoint can list/open them
        after the run. Written back when ``retain`` (debug-execute) OR ``keep_run``
        (UX-10e0 normal-run keep-latest). The ``wf_id`` is stamped onto every row
        so the NEXT run can purge this run's rows when a new run of the SAME
        workflow starts. A truly transient run (neither flag) skips the work —
        release would just delete the rows anyway."""
        if not (self.retain or self.keep_run):
            return 0
        return await sync_run_back(
            self.run_id, self.tenant_id, self.run_dir, self.wf_id)

    def sync_run_sync(self) -> int:
        """SYNC write-back of the run-tier ``/run`` files.

        Legal only from a genuinely synchronous caller. Mirrors
        :meth:`sync_run` so batch/deployment runs and canvas runs share the same
        VFS lifecycle when ``retain`` or ``keep_run`` is set.
        """
        if not (self.retain or self.keep_run):
            return 0
        return sync_run_back_sync(
            self.run_id, self.tenant_id, self.run_dir, self.wf_id)

    async def release(self) -> None:
        """Release run-tier metadata according to the retention flags."""
        await release_run(
            self.run_id, self.tenant_id, retain=self.retain,
            keep_run=self.keep_run)

    def release_sync(self) -> None:
        """Synchronous run-tier release."""
        try:
            PostgresVfsRunStore().release_sync(
                run_id=self.run_id,
                retain=self.retain or self.keep_run,
            )
        except Exception:  # pragma: no cover - fail-soft, sweep must still run
            logger.warning("run_release_failed", run_id=self.run_id,
                           retain=self.retain or self.keep_run,
                           site="run_workspace", exc_info=True)

    # --- async context manager ---------------------------------------------- #
    async def __aenter__(self) -> "RunWorkspace":
        await self._prepare_async()
        return self

    async def __aexit__(self, *exc) -> bool:
        # Return False so an in-block exception is never masked.
        try:
            await self.sync_run()
        except Exception:  # pragma: no cover - fail-soft, never mask the run
            logger.warning("run_writeback_failed", run_id=self.run_id,
                           site="run_workspace", exc_info=True)
        try:
            await self.sync_mount()
        except Exception:  # pragma: no cover - fail-soft, never mask the run
            logger.warning("user_mount_writeback_failed", run_id=self.run_id,
                           user_id=self.user_id, exc_info=True)
        try:
            await self.release()
        except Exception:  # pragma: no cover - fail-soft, never mask the run
            logger.warning("run_release_failed", run_id=self.run_id,
                           retain=self.retain, site="run_workspace", exc_info=True)
        remove_user_mount(self.mount_dir)
        self.mount_dir = None
        return False

    # --- sync context manager ----------------------------------------------- #
    def __enter__(self) -> "RunWorkspace":
        self._prepare_sync()
        return self

    def __exit__(self, *exc) -> bool:
        try:
            self.sync_run_sync()
        except Exception:  # pragma: no cover - fail-soft, never mask the run
            logger.warning("run_writeback_failed", run_id=self.run_id,
                           site="run_workspace", exc_info=True)
        try:
            self.sync_mount_sync()
        except Exception:  # pragma: no cover - fail-soft, never mask the run
            logger.warning("user_mount_writeback_failed", run_id=self.run_id,
                           user_id=self.user_id, exc_info=True)
        try:
            self.release_sync()
        except Exception:  # pragma: no cover - fail-soft, never mask the run
            logger.warning("run_release_failed", run_id=self.run_id,
                           retain=self.retain or self.keep_run,
                           site="run_workspace", exc_info=True)
        remove_user_mount(self.mount_dir)
        self.mount_dir = None
        return False

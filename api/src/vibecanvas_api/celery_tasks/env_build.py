"""Retired compatibility task for the old save-time overlay build path.

Dependency installation is now owned by sandboxd's narrow execution-time
builder capability. Keep the historical task name registered while old broker
messages drain, but never build directly in a general background worker.
"""
from __future__ import annotations

import structlog

from vibecanvas_api.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="build_env_overlay", bind=True)
def build_env_overlay(
    self,  # noqa: ARG001 — Celery ``bind=True`` passes the task instance.
    overlay_key: str,
    requirements: str,
) -> None:
    """Reject a queued legacy build without touching pip, files, or DB state."""
    logger.warning(
        "build_env_overlay_retired",
        overlay_key=overlay_key,
        requirements_present=bool(requirements.strip()),
        reason="dependency builds are owned by sandboxd execution preparation",
    )

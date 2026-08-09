"""Celery queue routing — forward-compat hook #1 (spec).

All ``send_task`` calls go through ``route_for(task_kind, deployment_id)``.
Today: ``interactive`` vs ``deployments`` split.
Future: multi-cluster — extend signature with tenant_id / cluster_hint and
select a cluster-scoped queue.
"""
from __future__ import annotations

import uuid
from typing import Optional


_KIND_TO_QUEUE: dict[str, str] = {
    "batch_exec": "interactive",
    "scheduled_run": "interactive",
    "agent_turn": "interactive",
    "deployment_invoke": "deployments",
    "kb_index_file": "kb_indexing",
}


def route_for(task_kind: str, deployment_id: Optional[uuid.UUID] = None) -> str:
    """Return the Celery queue name for the given task kind.

    ``deployment_id`` is unused today but accepted for forward-compat
    (multi-cluster routing will use it to pick a cluster-scoped queue).
    """
    queue = _KIND_TO_QUEUE.get(task_kind)
    if queue is None:
        raise ValueError(f"Unknown task_kind: {task_kind!r}")
    return queue

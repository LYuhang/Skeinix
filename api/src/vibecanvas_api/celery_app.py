"""Celery application for durable background tasks.

Spec: docs/superpowers/specs/2026-05-23-phase-6-async-celery-design.md §6.2.
- JSON serializer only (no pickle).
- Redis is both broker and result backend (latency-priority; durable audit
  goes to the tasks/task_events Postgres tables, not the Celery backend).
- Tasks autodiscovered from `vibecanvas_api.celery_tasks`.
- celery-beat is the scheduler used by the §6.3 submit reconciler.
"""

from celery import Celery
from celery.signals import worker_process_init, worker_ready

from vibecanvas_api.config import config
from vibecanvas_api.security_profile import (
    configured_cors_origins,
    validate_production_security,
)

_redis_url = config.redis.url  # e.g. "redis://localhost:6379/0"

# Celery worker and beat do not enter the FastAPI lifespan. Validate at import
# time so a production worker cannot run with a security profile the API would
# reject.
validate_production_security(config, cors_origins=configured_cors_origins())

celery_app = Celery(
    "vibecanvas",
    broker=_redis_url,
    backend=_redis_url,
    include=["vibecanvas_api.celery_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Reasonable defaults; tuned during execution.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)

# Explicit queue routing for tasks and deployments.
# Producers pass ``queue=`` via ``route_for()``; workers subscribe to
# ``CELERY_QUEUES``. No per-task routes (route_for is the single source).
celery_app.conf.task_default_queue = "interactive"
celery_app.conf.task_create_missing_queues = True

celery_app.autodiscover_tasks(["vibecanvas_api.celery_tasks"])


# Worker-side observability is imported
# lazily inside the handlers (not at module import) so this file stays free of
# any observability import cycle; the worker process initializes independently
# of the API process.


@worker_process_init.connect
def _init_worker_obs(**_kwargs):
    from vibecanvas_api.observability.celery import init_worker_observability

    init_worker_observability()


@worker_ready.connect
def _start_worker_metrics(**_kwargs):
    from vibecanvas_api.observability.celery import start_worker_metrics_server

    start_worker_metrics_server()

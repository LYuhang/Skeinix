import vibecanvas_api.celery_tasks  # noqa: F401 — trigger task autodiscovery so
# celery_app.tasks is populated when this module is run in isolation (the task
# modules register their @celery_app.task decorators on import; without this the
# manifest test sees an empty set under per-file pytest isolation).
from vibecanvas_api.celery_app import celery_app


def test_celery_app_configured():
    assert celery_app.main == "vibecanvas"
    # JSON-only serializer; pickle in a Celery
    # broker is a deserialization RCE vector; this codebase forbids it.
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert "pickle" not in celery_app.conf.accept_content
    assert celery_app.conf.result_serializer == "json"
    # Broker + result backend both Redis
    assert celery_app.conf.broker_url.startswith("redis://")
    assert celery_app.conf.result_backend.startswith("redis://")
    # Autodiscovers tasks under celery_tasks/
    assert "vibecanvas_api.celery_tasks" in celery_app.conf.include or \
           any("celery_tasks" in str(p) for p in celery_app.tasks)


def test_celery_app_registers_expected_tasks():
    """The set of user-defined tasks must match the runtime manifest.

    The Celery application includes batch execution, the beat reconciler, and
    ``deployment_invoke`` — the single task all three
    deployment trigger types (api / webhook / cron) funnel through.
    ``deployments.cron_dispatcher`` is the
    beat-scheduled sweep that discovers due cron deployments and
    enqueues ``deployment_invoke`` for each.
    ``deployments.flush_invoke_counters`` runs every 60 seconds
    beat — rolls Redis counters into ``deployments.invoke_count``) and
    while ``deployments.concurrency_reconciler`` runs daily to reconcile
    Redis per-tenant concurrency counters from Postgres truth.
    KB/RAG added ``kb.index_file`` + the ``kb.gc_sweeper`` /
    ``kb.orphan_reconciler`` beats. The retired ``build_env_overlay`` name remains
    registered only to consume old broker messages without installing
    anything; interactive Workflow execution owns dependency preparation.
    Future tasks will extend this set — adding a task
    here is a deliberate contract change, so the test fails loudly if an
    unexpected task appears (or an expected one regresses out).
    """
    user_tasks = {name for name in celery_app.tasks if not name.startswith("celery.")}
    expected = {
        "batch_exec",
        "deployment_invoke",
        "deployments.concurrency_reconciler",
        "deployments.cron_dispatcher",
        "deployments.flush_invoke_counters",
        "phase6.reconciler.resubmit_stuck_queued",
        "kb.index_file",
        "kb.gc_sweeper",
        "kb.orphan_reconciler",
        "build_env_overlay",
        "scheduled_runs.dispatch_due",
        "scheduled_runs.execute",
        "authorization.reconcile",
        "data_purge.run_due",
    }
    assert user_tasks == expected, (
        f"Registered Celery tasks {user_tasks} != expected {expected}"
    )

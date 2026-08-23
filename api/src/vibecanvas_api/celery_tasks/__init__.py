"""Celery task package autodiscovered by ``celery_app``.

Importing this package imports every task module so the
``@celery_app.task`` decorators register on the global Celery app
instance. The autodiscover call in ``celery_app.py`` triggers this
import at worker startup; importing the package from test code (e.g.
``from vibecanvas_api.celery_tasks.batch_exec import batch_exec``)
also achieves registration as a side effect.
"""
from vibecanvas_api.celery_tasks import batch_exec  # noqa: F401
from vibecanvas_api.celery_tasks import authorization_reconciler  # noqa: F401
from vibecanvas_api.celery_tasks import concurrency_reconciler  # noqa: F401
from vibecanvas_api.celery_tasks import deployment_invoke  # noqa: F401
from vibecanvas_api.celery_tasks import data_purge  # noqa: F401
from vibecanvas_api.celery_tasks import env_build  # noqa: F401
from vibecanvas_api.celery_tasks import invoke_counter_flush  # noqa: F401
from vibecanvas_api.celery_tasks import kb_gc_sweeper  # noqa: F401
from vibecanvas_api.celery_tasks import kb_indexer  # noqa: F401
from vibecanvas_api.celery_tasks import kb_orphan_reconciler  # noqa: F401
from vibecanvas_api.celery_tasks import reconciler  # noqa: F401
from vibecanvas_api.celery_tasks import scheduled_runs  # noqa: F401

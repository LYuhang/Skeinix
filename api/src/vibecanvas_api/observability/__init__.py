"""Observability package with three explicit entry points:

* configure_logging()            — call at import time, before uvicorn.run
* install_http_observability(app)— call in build_app(): middleware + /metrics
* init_tracing()                 — call in lifespan

Each is fail-safe: a failure inside observability never propagates to business
code (see D7). Implemented across logging.py / metrics.py / tracing.py."""
from __future__ import annotations

__all__ = ["configure_logging", "install_http_observability", "init_tracing"]


def configure_logging() -> None:
    from vibecanvas_api.observability.logging import configure_logging as _impl
    _impl()


def install_http_observability(app) -> None:
    # request-id middleware + /metrics endpoint + per-request FastAPI tracing
    # (SSE routes excluded via SSE_EXCLUDED_URLS inside instrument_fastapi_app).
    from vibecanvas_api.observability.middleware import install_request_id_middleware
    from vibecanvas_api.observability.metrics import install_metrics
    from vibecanvas_api.observability.tracing import instrument_fastapi_app
    install_request_id_middleware(app)
    install_metrics(app)
    instrument_fastapi_app(app)


def init_tracing() -> None:
    from vibecanvas_api.observability.tracing import init_tracing as _impl
    _impl()

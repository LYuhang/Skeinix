"""Structlog configuration with stdlib bridging and correlation IDs.

MUST be called at import time (before uvicorn boots its own dictConfig), or
uvicorn's loggers win. configure_logging() is idempotent.

KNOWN EXCEPTION: agent.py uses raw print(...) calls (e.g. lines ~407, 480, 487)
that bypass this structlog pipeline — they reach stdout/`docker logs` but are
NOT structured JSON. To be converted to a logger opportunistically (follow-up)."""
from __future__ import annotations

import logging
import sys

import structlog

from vibecanvas_api.config import config
from vibecanvas_api.observability import context
from vibecanvas_api.observability.otel import trace
from vibecanvas_api.security.redaction import structlog_redaction_processor

_CONFIGURED = False


class _StdoutProxy:
    """A write-through proxy that resolves ``sys.stdout`` *lazily* on every
    write. ``configure_logging()`` runs at import time (before uvicorn boots),
    so a ``StreamHandler(sys.stdout)`` would capture whatever stdout object
    existed at import and hold it forever. That breaks test capture (pytest's
    ``capsys`` swaps ``sys.stdout`` per-test, after import) and any later
    stdout redirection. Resolving live keeps both production (real stdout) and
    tests (captured stdout) correct without re-running configure_logging."""

    def write(self, data):  # noqa: D401 - file-like
        return sys.stdout.write(data)

    def flush(self):
        return sys.stdout.flush()


def _add_correlation_ids(_logger, _method, event_dict):
    rid = context.get_request_id()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    tid = context.get_tenant_id()
    if tid is not None:
        event_dict.setdefault("tenant_id", tid)
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx is not None and ctx.is_valid:
        event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
    return event_dict


def configure_logging(*, force_format: str | None = None) -> None:
    global _CONFIGURED
    fmt = force_format or config.observability.log_format
    level = getattr(logging, config.observability.log_level, logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_correlation_ids,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Keep this last among shared processors: exception text and stdlib
        # positional arguments have already been rendered enough to redact.
        structlog_redaction_processor,
    ]
    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    # structlog -> stdlib so third-party logs share the pipeline
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(_StdoutProxy())
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Alembic and some server/test harnesses legitimately own the root logger
    # and may reconfigure it after application import. Give product logs an
    # explicit parent pipeline so Agent/Runtime timing remains visible even
    # when a third party later raises the root level. fileConfig() can also
    # mark already-created loggers disabled, so restore every product child.
    product_logger = logging.getLogger("vibecanvas_api")
    product_logger.handlers = [handler]
    product_logger.setLevel(level)
    product_logger.propagate = False
    product_logger.disabled = False
    for logger_name, logger_value in logging.root.manager.loggerDict.items():
        if not logger_name.startswith("vibecanvas_api."):
            continue
        if isinstance(logger_value, logging.Logger):
            logger_value.disabled = False
            logger_value.setLevel(logging.NOTSET)
    # re-point uvicorn loggers at our handler (they may already be configured)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
    _CONFIGURED = True

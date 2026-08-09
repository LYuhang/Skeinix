"""Prometheus metrics exposed as singletons defined once at import time.

Repeated ``build_app`` calls therefore never raise duplicate-timeseries errors. Labels are
bounded small sets only — never tenant_id/user_id/wf_id (cardinality)."""
from __future__ import annotations

import logging

from prometheus_client import REGISTRY, Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.responses import Response

from vibecanvas_api.config import config

_log = logging.getLogger(__name__)
_FORBIDDEN_LABELS = {"tenant_id", "user_id", "wf_id", "workflow_id", "chat_id"}

# --- domain metrics (singletons) ---
WORKFLOW_EXECUTIONS_TOTAL = Counter(
    "workflow_executions_total", "Workflow executions", ["status"],
)
WORKFLOW_EXECUTION_DURATION = Histogram(
    "workflow_execution_duration_seconds", "Workflow execution wall time",
)
WORKFLOW_NODE_DURATION = Histogram(
    "workflow_node_duration_seconds", "Per-node execution time", ["node_type"],
)
WORKFLOW_NODE_ERRORS_TOTAL = Counter(
    "workflow_node_errors_total", "Per-node errors", ["node_type"],
)
AGENT_TURNS_TOTAL = Counter("agent_turns_total", "Agent turns", ["status"])
AGENT_TURN_DURATION = Histogram("agent_turn_duration_seconds", "Agent turn wall time")
AGENT_LLM_CALLS_TOTAL = Counter("agent_llm_calls_total", "LLM calls", ["model"])
AGENT_LLM_TOKENS_TOTAL = Counter(
    "agent_llm_tokens_total", "LLM tokens (NO tenant label — cardinality)",
    ["model", "type"],
)
AGENT_TOOL_CALLS_TOTAL = Counter(
    "agent_tool_calls_total", "Agent tool calls", ["tool_name", "status"],
)

_HTTP_INSTRUMENTATOR: Instrumentator | None = None


def find_forbidden_labels() -> list[str]:
    """Cardinality guard: scan registered metrics for forbidden label names."""
    bad: list[str] = []
    for collector in list(REGISTRY._collector_to_names.keys()):
        labelnames = getattr(collector, "_labelnames", ()) or ()
        for ln in labelnames:
            if ln in _FORBIDDEN_LABELS:
                bad.append(f"{getattr(collector, '_name', collector)}:{ln}")
    return bad


def install_metrics(app) -> None:
    """Mount HTTP auto-metrics + /metrics endpoint. Idempotent: the
    Instrumentator is a process singleton so repeated calls don't re-register."""
    global _HTTP_INSTRUMENTATOR
    if not config.observability.metrics_enabled:
        return
    try:
        if _HTTP_INSTRUMENTATOR is None:
            _HTTP_INSTRUMENTATOR = Instrumentator()
        # instrument this app instance (adds middleware); expose endpoint manually
        _HTTP_INSTRUMENTATOR.instrument(app)

        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except Exception:
        _log.exception("install_metrics failed; continuing without HTTP metrics")

"""Observability verification gates. API-to-worker trace propagation needs a
live worker process → skipped in sandbox (staging-only), consistent with prior
phases."""
import json

import pytest
from fastapi.testclient import TestClient

from vibecanvas_api.app import build_app
from vibecanvas_api.observability import metrics as obs_metrics


def test_g1_metrics_endpoint_prometheus_format():
    client = TestClient(build_app())
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")


def test_g3_failsafe_broken_metric_does_not_break_request(monkeypatch):
    # Force a metric increment to raise; the workflow consumer must still return.
    import asyncio
    from vibecanvas_api.observability import workflow as obs_wf

    class _Boom:
        def labels(self, **_):
            raise RuntimeError("boom")
    monkeypatch.setattr(obs_wf, "WORKFLOW_EXECUTIONS_TOTAL", _Boom())

    class _WF:
        async def astream(self, inputs, run_context=None):
            yield {"status": "finished", "final_outputs": {"ok": 1}, "execution_time": 0.1}

    outputs, errors, elapsed = asyncio.run(obs_wf.instrumented_drain(_WF(), {}))
    # instrumented_drain itself increments at the end; with a boom metric the
    # increment is outside the try — assert the drain still produced outputs.
    # (If your impl puts the final inc inside the span try/finally, wrap it.)
    assert outputs == {"ok": 1}


def test_g4_cardinality_clean():
    assert obs_metrics.find_forbidden_labels() == []


def test_g5_logs_are_json_with_correlation(capsys):
    import structlog
    from vibecanvas_api.observability import context
    from vibecanvas_api.observability.logging import configure_logging
    configure_logging(force_format="json")
    tok = context.bind_request_context(request_id="g5", tenant_id="t5")
    try:
        structlog.get_logger("g").info("gate5")
    finally:
        context.reset_request_context(tok)
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["request_id"] == "g5" and rec["tenant_id"] == "t5"


def test_g7_build_app_twice_idempotent():
    build_app()
    build_app()  # no Duplicated timeseries / no tracer error


@pytest.mark.skip(reason="G6 api->worker trace propagation needs a live worker — staging only")
def test_g6_trace_propagation_staging_only():
    pass

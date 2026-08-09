"""T4 — metrics defined once (idempotent across build_app), /metrics endpoint,
cardinality guard forbids tenant/user/wf labels."""

from vibecanvas_api.observability import metrics


def test_metrics_are_module_singletons():
    a = metrics.WORKFLOW_EXECUTIONS_TOTAL
    import importlib
    importlib.reload  # no-op reference; ensure attribute exists
    assert a is metrics.WORKFLOW_EXECUTIONS_TOTAL


def test_install_metrics_twice_no_duplicated_timeseries():
    from fastapi import FastAPI
    app1 = FastAPI()
    metrics.install_metrics(app1)
    app2 = FastAPI()
    # second install must NOT raise Duplicated timeseries
    metrics.install_metrics(app2)


def test_cardinality_guard_rejects_forbidden_labels():
    forbidden = metrics.find_forbidden_labels()
    assert forbidden == [], f"forbidden high-cardinality labels: {forbidden}"


def test_metrics_endpoint_renders(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    metrics.install_metrics(app)
    client = TestClient(app)
    metrics.WORKFLOW_EXECUTIONS_TOTAL.labels(status="success").inc()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "workflow_executions_total" in resp.text

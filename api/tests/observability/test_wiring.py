"""T8 — build_app twice is idempotent (no Duplicated timeseries); /metrics and
request-id are wired; /healthz still works; FastAPI request tracing is active
(per-request HTTP server span) with SSE routes excluded."""
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from vibecanvas_api.app import build_app
from vibecanvas_api.observability import tracing


def test_build_app_twice_no_duplicated_timeseries():
    app1 = build_app()
    app2 = build_app()  # must not raise — incl. FastAPIInstrumentor.instrument_app
    assert app1 is not app2


def test_metrics_and_request_id_wired():
    app = build_app()
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert "X-Request-ID" in r.headers
    m = client.get("/metrics")
    assert m.status_code == 200
    assert "http_request" in m.text or "python_info" in m.text


@pytest.fixture
def span_exporter():
    """Attach an InMemorySpanExporter to the live (set-once) provider so we can
    observe the spans the FastAPI instrumentor emits for real HTTP requests."""
    tracing.init_tracing()  # ensure provider exists (idempotent)
    exporter = InMemorySpanExporter()
    tracing.add_span_processor_for_tests(SimpleSpanProcessor(exporter))
    return exporter


def _server_spans(exporter):
    return [s for s in exporter.get_finished_spans() if s.kind == SpanKind.SERVER]


def test_http_request_creates_root_server_span(span_exporter):
    """A normal (non-SSE) endpoint must now produce a FastAPI SERVER span —
    proving install_http_observability actually instruments the app."""
    app = build_app()
    client = TestClient(app)
    span_exporter.clear()

    r = client.get("/healthz")
    assert r.status_code == 200

    server_spans = _server_spans(span_exporter)
    assert server_spans, "no HTTP server span recorded — FastAPI tracing inert"
    assert any("/healthz" in (s.name or "") or
               s.attributes.get("http.route") == "/healthz" or
               s.attributes.get("http.target") == "/healthz"
               for s in server_spans), [
        (s.name, dict(s.attributes)) for s in server_spans
    ]


def test_sse_route_is_excluded_from_tracing(span_exporter):
    """SSE_EXCLUDED_URLS must keep streaming routes uninstrumented: hitting a
    path matching the exclusion (/stream) must NOT create a server span."""
    # Sanity: the exclusion pattern is actually handed to the instrumentor.
    assert "/stream" in tracing.SSE_EXCLUDED_URLS

    app = build_app()
    client = TestClient(app)
    span_exporter.clear()

    # An SSE route whose path contains "/stream"; we don't need it to succeed,
    # only to be routed through the (excluded) instrumented app. Auth/validation
    # errors are fine — the point is no SERVER span is created for this path.
    client.get("/api/v1/tasks/does-not-exist/stream")

    sse_spans = [
        s for s in _server_spans(span_exporter)
        if "/stream" in (s.attributes.get("http.target") or "")
        or "/stream" in (s.attributes.get("http.route") or "")
        or "stream" in (s.name or "")
    ]
    assert not sse_spans, [
        (s.name, dict(s.attributes)) for s in sse_spans
    ]

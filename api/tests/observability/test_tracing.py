"""T3 — tracing init is idempotent (set-once) and produces spans into an
in-memory exporter; export stays off by default."""
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from vibecanvas_api.observability import tracing


def test_init_tracing_is_idempotent():
    tracing.init_tracing()
    provider_1 = trace.get_tracer_provider()
    tracing.init_tracing()  # second call must NOT replace the provider
    provider_2 = trace.get_tracer_provider()
    assert provider_1 is provider_2


def test_spans_are_recorded_via_inmemory_exporter():
    tracing.init_tracing()
    exporter = InMemorySpanExporter()
    tracing.add_span_processor_for_tests(SimpleSpanProcessor(exporter))
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("unit-span"):
        pass
    spans = exporter.get_finished_spans()
    assert any(s.name == "unit-span" for s in spans)


def test_export_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OTEL_TRACES_ENABLED", raising=False)
    # No OTLP processor should be added when export is off; init must not raise
    tracing.init_tracing()


@pytest.fixture
def reset_tracing_state():
    """init_tracing is set-once; earlier tests may have initialized it. Reset
    both our module guard AND OTel's global SET_ONCE guard so init_tracing's
    set_tracer_provider() actually registers (otherwise it's a no-op and the
    test cannot observe the churn it's guarding against). Restore afterwards."""
    from opentelemetry.util._once import Once

    saved_initialized = tracing._INITIALIZED
    saved_provider = tracing._PROVIDER
    saved_global = trace._TRACER_PROVIDER
    saved_once = trace._TRACER_PROVIDER_SET_ONCE

    tracing._INITIALIZED = False
    tracing._PROVIDER = None
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    try:
        yield
    finally:
        tracing._INITIALIZED = saved_initialized
        tracing._PROVIDER = saved_provider
        trace._TRACER_PROVIDER = saved_global
        trace._TRACER_PROVIDER_SET_ONCE = saved_once


def test_instrumentor_failure_does_not_churn_provider(monkeypatch, reset_tracing_state):
    # An instrumentor failure must not leave the provider uncommitted: the
    # global provider must equal tracing._PROVIDER, and add_span_processor_for_tests
    # must reach the registered global so spans are captured.
    def _boom() -> None:
        raise RuntimeError("instrumentor blew up")

    monkeypatch.setattr(tracing, "_install_auto_instrumentors", _boom)
    tracing.init_tracing()

    # Provider committed despite the instrumentor failure, and it IS the global.
    assert tracing._PROVIDER is not None
    assert trace.get_tracer_provider() is tracing._PROVIDER

    # A processor attached after init reaches the registered global provider.
    exporter = InMemorySpanExporter()
    tracing.add_span_processor_for_tests(SimpleSpanProcessor(exporter))
    tracer = trace.get_tracer("churn-test")
    with tracer.start_as_current_span("churn-span"):
        pass
    spans = exporter.get_finished_spans()
    assert any(s.name == "churn-span" for s in spans)

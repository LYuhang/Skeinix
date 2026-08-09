"""T5 — consuming the engine astream event stream produces a workflow.execute
span + retroactive per-node spans, and increments workflow metrics. Both the
async (drain_astream) and the sync (run_workflow_sync) call sites use the same
instrumented consumer."""
import asyncio

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from vibecanvas_api.observability import tracing
from vibecanvas_api.observability.metrics import WORKFLOW_EXECUTIONS_TOTAL
from vibecanvas_api.observability import workflow as obs_wf


class _FakeWorkflow:
    """Emits engine-shaped events: per-node success then a finished event."""
    def __init__(self, events):
        self._events = events

    async def astream(self, inputs, run_context=None):
        for ev in self._events:
            yield ev


def _events():
    return [
        {"status": "success", "node_id": "n1", "node_type": "CodeNode",
         "execution_time": 0.5, "span_id": "s1", "parent_span_id": None, "trace_id": "tr1"},
        {"status": "success", "node_id": "n2", "node_type": "PromptNode",
         "execution_time": 1.0, "span_id": "s2", "parent_span_id": "s1", "trace_id": "tr1"},
        {"status": "finished", "final_outputs": {"ok": 1}, "error_dict": {}, "execution_time": 1.6},
    ]


def test_instrumented_drain_emits_spans_and_metrics():
    tracing.init_tracing()
    exporter = InMemorySpanExporter()
    tracing.add_span_processor_for_tests(SimpleSpanProcessor(exporter))
    before = WORKFLOW_EXECUTIONS_TOTAL.labels(status="success")._value.get()

    wf = _FakeWorkflow(_events())
    outputs, errors, elapsed = asyncio.run(obs_wf.instrumented_drain(wf, {}))

    assert outputs == {"ok": 1}
    names = [s.name for s in exporter.get_finished_spans()]
    assert "workflow.execute" in names
    assert names.count("workflow.node") >= 2  # two node spans
    after = WORKFLOW_EXECUTIONS_TOTAL.labels(status="success")._value.get()
    assert after == before + 1


def test_instrumented_drain_preserves_engine_error_keying():
    """Parity guard: engine-level ``error`` events stay keyed by node_id /
    ``__engine__`` (matches ``_trigger_inner``/``drain_astream``), and the
    ``finished`` event's bundled ``error_dict`` is merged, so existing callers
    (batch_exec iterates ``errors.items()``) see the same shape."""
    tracing.init_tracing()
    events = [
        {"status": "error", "node_id": "n9", "node_type": "CodeNode",
         "error_message": "boom", "execution_time": 0.1},
        {"status": "finished", "final_outputs": {}, "error_dict": {"n5": {"x": 1}},
         "execution_time": 0.2},
    ]

    class _W:
        async def astream(self, inputs, run_context=None):
            for ev in events:
                yield ev

    outputs, errors, elapsed = asyncio.run(obs_wf.instrumented_drain(_W(), {}))
    assert errors == {"n9": "boom", "n5": {"x": 1}}

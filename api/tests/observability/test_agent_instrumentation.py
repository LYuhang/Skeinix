"""T6 — recording an LLM result increments token + call metrics by model, and
emits a per-tenant token log line (NOT a metric label)."""

from vibecanvas_api.observability import agent as obs_agent
from vibecanvas_api.observability.logging import configure_logging
from vibecanvas_api.observability.metrics import AGENT_LLM_TOKENS_TOTAL, AGENT_LLM_CALLS_TOTAL


def test_record_llm_usage_increments_by_model(capsys):
    # T8 note: configure our structlog->stdlib JSON pipeline explicitly so the
    # usage log lands on stdout deterministically. Without this the test relied
    # on structlog's *unconfigured* default PrintLogger writing to stdout — once
    # the app configures logging at import time (T8), structlog routes through
    # stdlib + pytest-owned root handlers and the bare assertion became
    # order-dependent. configure_logging() installs a handler on a stdout proxy
    # at INFO level, so the INFO usage line is captured here regardless.
    configure_logging(force_format="json")
    before_tok = AGENT_LLM_TOKENS_TOTAL.labels(model="gpt-x", type="prompt")._value.get()
    before_call = AGENT_LLM_CALLS_TOTAL.labels(model="gpt-x")._value.get()

    obs_agent.record_llm_usage(
        model="gpt-x",
        usage_metadata={"input_tokens": 10, "output_tokens": 4},
        tenant_id="ten-9",
    )

    assert AGENT_LLM_TOKENS_TOTAL.labels(model="gpt-x", type="prompt")._value.get() == before_tok + 10
    assert AGENT_LLM_CALLS_TOTAL.labels(model="gpt-x")._value.get() == before_call + 1
    # per-tenant token usage is a LOG line, not a metric label
    out = capsys.readouterr().out
    assert "ten-9" in out


def test_record_llm_usage_is_fail_safe_on_bad_input():
    # malformed usage must not raise (fail-safe)
    obs_agent.record_llm_usage(model=None, usage_metadata=None, tenant_id=None)

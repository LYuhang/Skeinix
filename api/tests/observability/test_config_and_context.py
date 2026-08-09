"""T1 — ObservabilityConfig defaults + contextvar bind/get."""

from vibecanvas_api.config import config
from vibecanvas_api.observability import context


def test_observability_config_defaults():
    obs = config.observability
    assert obs.log_level == "INFO"
    assert obs.log_format == "json"
    assert obs.metrics_enabled is True
    assert obs.otel_traces_enabled is False


def test_contextvars_default_none_then_bind():
    # defaults are None when nothing bound
    assert context.get_request_id() is None
    assert context.get_tenant_id() is None
    token = context.bind_request_context(request_id="req-1", tenant_id="t-1")
    try:
        assert context.get_request_id() == "req-1"
        assert context.get_tenant_id() == "t-1"
    finally:
        context.reset_request_context(token)
    assert context.get_request_id() is None


def test_log_format_env_override(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "console")
    from vibecanvas_api.config import ObservabilityConfig
    obs = ObservabilityConfig({})
    assert obs.log_format == "console"

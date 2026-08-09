"""T2 — structlog JSON output carries correlation ids; middleware binds them."""
import json
import logging

import structlog

from vibecanvas_api.observability import context
from vibecanvas_api.observability.logging import configure_logging


def test_json_log_carries_request_and_tenant(capsys):
    configure_logging(force_format="json")
    token = context.bind_request_context(request_id="req-42", tenant_id="ten-7")
    try:
        structlog.get_logger("test").info("hello", foo="bar")
    finally:
        context.reset_request_context(token)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(out)
    assert rec["event"] == "hello"
    assert rec["foo"] == "bar"
    assert rec["request_id"] == "req-42"
    assert rec["tenant_id"] == "ten-7"


def test_stdlib_logging_is_bridged_to_json(capsys):
    configure_logging(force_format="json")
    logging.getLogger("some.thirdparty").warning("via stdlib %s", "x")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(out)
    assert rec["event"] == "via stdlib x"
    assert rec["level"] == "warning"


def test_console_format_is_not_json(capsys):
    configure_logging(force_format="console")
    structlog.get_logger("test").info("plain")
    out = capsys.readouterr().out
    assert "plain" in out  # human-readable, not strict JSON object


def test_product_logs_survive_root_level_override(capsys):
    configure_logging(force_format="json")
    logging.getLogger().setLevel(logging.WARNING)
    structlog.get_logger("vibecanvas_api.services.agent_runtime").info(
        "agent_runtime_timing",
        elapsed_ms=17,
    )
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["event"] == "agent_runtime_timing"
    assert rec["elapsed_ms"] == 17

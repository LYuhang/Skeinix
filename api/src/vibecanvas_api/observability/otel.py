"""OpenTelemetry compatibility shim.

Observability must be fail-soft: a partially installed OpenTelemetry namespace
package should not prevent the API app, tests, or local tools from importing.
When the real OTel API is unavailable this module exposes a tiny no-op subset
used by the codebase.
"""
from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from typing import Any, Iterator


class _NoopSpanContext:
    is_valid = False
    trace_id = 0


class _NoopSpan:
    def get_span_context(self) -> _NoopSpanContext:
        return _NoopSpanContext()

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def end(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class _NoopStatusCode(Enum):
    ERROR = "ERROR"


class _NoopStatus:
    def __init__(self, status_code: Any, description: str | None = None):
        self.status_code = status_code
        self.description = description


class _NoopTracer:
    def start_span(self, *_args: Any, **_kwargs: Any) -> _NoopSpan:
        return _NoopSpan()

    @contextmanager
    def start_as_current_span(self, *_args: Any, **_kwargs: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


class _NoopTrace:
    Status = _NoopStatus
    StatusCode = _NoopStatusCode

    @staticmethod
    def get_tracer(_name: str) -> _NoopTracer:
        return _NoopTracer()

    @staticmethod
    def get_current_span() -> _NoopSpan:
        return _NoopSpan()

    @staticmethod
    def set_tracer_provider(_provider: Any) -> None:
        return None


try:
    from opentelemetry import trace as trace  # type: ignore
except Exception:
    trace = _NoopTrace()  # type: ignore


__all__ = ["trace"]

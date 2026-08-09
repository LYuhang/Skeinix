"""Request/task-scoped context variables shared by logging and middleware.

These carry correlation IDs across async boundaries. trace_id is read live
from the active OTel span at log-render time (see logging.py), so it is NOT
stored here."""
from __future__ import annotations

import contextvars
from typing import NamedTuple

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vibecanvas_request_id", default=None
)
_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vibecanvas_obs_tenant_id", default=None
)


class _CtxTokens(NamedTuple):
    request_id: contextvars.Token
    tenant_id: contextvars.Token


def bind_request_context(*, request_id: str | None, tenant_id: str | None) -> _CtxTokens:
    return _CtxTokens(
        request_id=_request_id.set(request_id),
        tenant_id=_tenant_id.set(tenant_id),
    )


def reset_request_context(tokens: _CtxTokens) -> None:
    _request_id.reset(tokens.request_id)
    _tenant_id.reset(tokens.tenant_id)


def get_request_id() -> str | None:
    return _request_id.get()


def get_tenant_id() -> str | None:
    return _tenant_id.get()


def bind_tenant_id(tenant_id: str | None) -> None:
    """Set the correlation tenant_id for the rest of the current request
    context (called once auth resolves the tenant). Fire-and-forget: the
    per-request ASGI contextvar context is isolated, and the request-id
    middleware's reset restores the baseline at request end."""
    _tenant_id.set(tenant_id)

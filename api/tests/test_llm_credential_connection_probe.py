"""DB-free tests for the sanitized LLM credential connection probe."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import httpx
import pytest

from vibecanvas_api.routes import llm_credentials as routes


class _Repo:
    def __init__(self, _session) -> None:
        pass

    async def get(self, credential_id: uuid.UUID) -> dict:
        return {
            "id": credential_id,
            "tenant_id": uuid.uuid4(),
            "provider": "openai",
            "enabled": True,
            "secret_ref": uuid.uuid4(),
        }


class _Secrets:
    async def resolve_text(self, *_args, **_kwargs) -> str:
        return "test-secret-that-must-not-be-returned"


class _Stream:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _Client:
    status_code = 200
    last_url = ""
    last_headers: dict[str, str] = {}

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, url: str, *, headers: dict[str, str]):
        type(self).last_url = url
        type(self).last_headers = headers
        return _Stream(type(self).status_code)


@pytest.fixture
def probe_dependencies(monkeypatch):
    async def _authorize(**_kwargs) -> None:
        return None

    async def _hydrate(_session, _row) -> dict:
        return {"api_url": "https://models.example.test/v1", "proxy": None}

    async def _validate(url: str, *, label: str):
        assert label == "model API URL"
        return url, "models.example.test", ("203.0.113.10",)

    monkeypatch.setattr(routes, "_authorize_credential", _authorize)
    monkeypatch.setattr(routes, "LlmCredentialsRepo", _Repo)
    monkeypatch.setattr(routes, "hydrate_llm_connection_credentials", _hydrate)
    monkeypatch.setattr(routes, "_validated_user_destination", _validate)
    monkeypatch.setattr(routes, "secret_service", lambda: _Secrets())
    monkeypatch.setattr(routes, "PinnedAsyncHTTPTransport", lambda **_kwargs: object())
    monkeypatch.setattr(routes.httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "outcome", "ok"),
    [
        (200, "connected", True),
        (401, "credentials_rejected", False),
        (503, "endpoint_rejected", False),
    ],
)
async def test_connection_probe_returns_only_sanitized_status(
    probe_dependencies,
    status_code: int,
    outcome: str,
    ok: bool,
) -> None:
    _Client.status_code = status_code
    result = await routes.test_credential_connection(
        uuid.uuid4(),
        request=object(),
        ctx=SimpleNamespace(active_organization_id=str(uuid.uuid4())),
        session=object(),
        service=object(),
    )

    assert result.outcome == outcome
    assert result.ok is ok
    assert result.upstream_status == status_code
    assert result.latency_ms >= 0
    assert _Client.last_url == "https://models.example.test/v1/models"
    assert _Client.last_headers == {
        "Accept": "application/json",
        "Authorization": "Bearer test-secret-that-must-not-be-returned",
    }
    assert "test-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_connection_probe_sanitizes_network_failures(
    probe_dependencies,
    monkeypatch,
) -> None:
    class _OfflineClient(_Client):
        async def __aenter__(self):
            raise httpx.ConnectError("provider detail must not escape")

    monkeypatch.setattr(routes.httpx, "AsyncClient", _OfflineClient)
    result = await routes.test_credential_connection(
        uuid.uuid4(),
        request=object(),
        ctx=SimpleNamespace(active_organization_id=str(uuid.uuid4())),
        session=object(),
        service=object(),
    )

    assert result.outcome == "unreachable"
    assert result.ok is False
    assert result.upstream_status is None
    assert "provider detail" not in result.model_dump_json()

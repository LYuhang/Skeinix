from __future__ import annotations

import uuid

import pytest
from vibecanvas_api.services.agent_runtime import capabilities as capabilities_module
from vibecanvas_api.services.agent_runtime.capabilities import (
    CODEX_CREDENTIAL_PREFIX,
    CODEX_OPENROUTER_PREFIX,
    LANGCHAIN_CREDENTIAL_PREFIX,
    codex_openrouter_model,
    codex_capabilities,
    codex_credential_id,
    langchain_capabilities,
    langchain_credential_id,
    runtime_model_connection_id,
    validate_model_effort,
)
from vibecanvas_api.services.agent_runtime.compatibility import (
    API_SOURCE_REGISTRY,
    compatible_api,
)
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeCapabilities,
    RuntimeModelOption,
    RuntimeReasoningEffortOption,
    RuntimeType,
)


class _ConnectedCodexAccount:
    def __init__(self, _tenant_id: str, _user_id: str) -> None:
        pass

    async def status(self):
        return type("Status", (), {"authenticated": True})()

    async def list_models(self):
        return [
            RuntimeModelOption(
                id="gpt-account-fast",
                label="Account fast",
                provider="chatgpt",
            ),
            RuntimeModelOption(
                id="gpt-account-default",
                label="Account default",
                provider="chatgpt",
                is_default=True,
            ),
        ]


def test_langchain_catalog_uses_opaque_selection_ids_and_model_scoped_efforts(
    monkeypatch,
):
    monkeypatch.setattr(
        capabilities_module.config.agent, "model", "openai:gpt-platform"
    )
    monkeypatch.setattr(capabilities_module.config.agent, "api_key", "platform-secret")
    credential_id = uuid.uuid4()
    capabilities = langchain_capabilities([{
        "id": credential_id,
        "name": "Team reasoning model",
        "provider": "openai",
        "model_name": "gpt-team",
    }])

    selected = capabilities.models[1]
    assert selected.id == f"{LANGCHAIN_CREDENTIAL_PREFIX}{credential_id}"
    assert selected.description == "openai · gpt-team"
    assert [option.id for option in selected.supported_reasoning_efforts] == [
        "minimal", "low", "medium", "high", "xhigh",
    ]
    assert langchain_credential_id(selected.id) == credential_id


def test_langchain_catalog_does_not_invent_a_default_api(monkeypatch):
    monkeypatch.setattr(capabilities_module.config.agent, "model", "")
    monkeypatch.setattr(capabilities_module.config.agent, "api_key", "")

    capabilities = langchain_capabilities([])

    assert capabilities.runtime_available is True
    assert capabilities.authenticated is False
    assert capabilities.models == []
    assert capabilities.default_model_id is None
    assert capabilities.error_code == "langchain_model_unavailable"


def test_langchain_catalog_selects_the_real_user_api_without_a_synthetic_default(
    monkeypatch,
):
    monkeypatch.setattr(capabilities_module.config.agent, "model", "")
    monkeypatch.setattr(capabilities_module.config.agent, "api_key", "")
    credential_id = uuid.uuid4()

    capabilities = langchain_capabilities([{
        "id": credential_id,
        "name": "My explicit API",
        "provider": "openai",
        "model_name": "gpt-explicit",
    }])

    expected = f"{LANGCHAIN_CREDENTIAL_PREFIX}{credential_id}"
    assert [model.id for model in capabilities.models] == [expected]
    assert capabilities.default_model_id == expected
    assert langchain_credential_id(capabilities.default_model_id) == credential_id


def test_runtime_defined_effort_names_are_not_platform_enums():
    capabilities = RuntimeCapabilities(
        runtime_type=RuntimeType.CODEX,
        runtime_available=True,
        source="test",
        models=[RuntimeModelOption(
            id="codex-model",
            label="Codex model",
            is_default=True,
            default_reasoning_effort="ultra",
            supported_reasoning_efforts=[
                RuntimeReasoningEffortOption(id="ultra", label="Ultra")
            ],
        )],
        default_model_id="codex-model",
    )

    assert validate_model_effort(
        capabilities, model_id=None, reasoning_effort="ultra"
    ).id == "codex-model"
    with pytest.raises(ValueError, match="reasoning_effort_not_supported_by_model"):
        validate_model_effort(
            capabilities, model_id="codex-model", reasoning_effort="medium"
        )


def test_runtime_api_registry_separates_source_provider_and_protocol():
    assert API_SOURCE_REGISTRY["openrouter_oauth"].authentication == "oauth_pkce"
    langchain = compatible_api(
        RuntimeType.LANGCHAIN,
        api_source="openrouter_oauth",
        provider="openrouter",
    )
    codex = compatible_api(
        RuntimeType.CODEX,
        api_source="openrouter_oauth",
        provider="openrouter",
    )
    assert langchain is not None
    assert langchain.api_protocol == "openai_compatible"
    assert codex is not None
    assert codex.api_protocol == "openai_responses"
    assert compatible_api(
        RuntimeType.CODEX,
        api_source="manual",
        provider="anthropic",
    ) is None


def test_runtime_connection_ids_preserve_the_exact_non_secret_source():
    credential_id = "11111111-1111-4111-8111-111111111111"
    assert runtime_model_connection_id(
        RuntimeType.LANGCHAIN,
        f"langchain:credential:{credential_id}",
    ) == f"langchain:credential:{credential_id}"
    assert runtime_model_connection_id(
        RuntimeType.LANGCHAIN,
        f"langchain:openrouter:{credential_id}:encoded-model",
    ) == f"langchain:openrouter:{credential_id}"
    assert runtime_model_connection_id(
        RuntimeType.CODEX,
        f"codex:openrouter:{credential_id}:encoded-model",
    ) == f"codex:openrouter:{credential_id}"
    assert runtime_model_connection_id(
        RuntimeType.CODEX,
        "codex:managed:company:model-a",
    ) == "codex:managed:company"


@pytest.mark.asyncio
async def test_codex_catalog_projects_each_openrouter_model_and_effort(monkeypatch):
    credential_id = uuid.uuid4()
    monkeypatch.setattr(
        capabilities_module,
        "resolve_codex_executable",
        lambda: "/opt/codex/bin/codex",
    )
    capabilities = await codex_capabilities([{
        "id": credential_id,
        "name": "OpenRouter",
        "provider": "openrouter",
        "connection_kind": "openrouter_oauth",
        "model_name": "openrouter/auto",
        "model_catalog": [{
            "id": "stealth/ox-alpha",
            "name": "Ox Alpha",
            "description": "Free agent model",
            "context_length": 1_048_576,
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "supports_tools": True,
            "supported_reasoning_efforts": ["low", "high", "max"],
            "default_reasoning_effort": "max",
            "pricing": {"prompt": "0", "completion": "0"},
            "available": True,
        }],
    }], auth_methods=["personal_api"])

    assert len(capabilities.models) == 1
    model = capabilities.models[0]
    assert model.id.startswith(f"{CODEX_OPENROUTER_PREFIX}{credential_id}:")
    assert model.api_source == "openrouter_oauth"
    assert model.api_protocol == "openai_responses"
    assert model.provider_model_id == "stealth/ox-alpha"
    assert [effort.id for effort in model.supported_reasoning_efforts] == [
        "low", "high", "max",
    ]
    assert model.default_reasoning_effort == "max"
    assert codex_openrouter_model(model.id) == "stealth/ox-alpha"


@pytest.mark.asyncio
async def test_codex_catalog_uses_only_responses_compatible_host_broker_models(
    monkeypatch,
):
    openai_id = uuid.uuid4()
    anthropic_id = uuid.uuid4()
    monkeypatch.setattr(
        capabilities_module,
        "resolve_codex_executable",
        lambda: "/opt/codex/bin/codex",
    )
    # LangChain may still have a deployment-level fallback; Codex must never
    # inherit it as an implicit API connection.
    monkeypatch.setattr(
        capabilities_module.config.agent, "model", "openai:gpt-platform"
    )

    capabilities = await codex_capabilities([
        {
            "id": openai_id,
            "name": "Team Codex model",
            "provider": "openai",
            "runtime_scope": "codex",
            "model_name": "gpt-team",
        },
        {
            "id": anthropic_id,
            "name": "Native Anthropic",
            "provider": "anthropic",
            "runtime_scope": "codex",
            "model_name": "claude-native",
        },
    ])

    assert capabilities.source == "codex.app-server+runtime-model-broker"
    assert capabilities.authenticated is True
    assert [item.id for item in capabilities.models] == [
        f"{CODEX_CREDENTIAL_PREFIX}{openai_id}",
    ]
    assert codex_credential_id(capabilities.models[0].id) == openai_id


@pytest.mark.asyncio
async def test_codex_catalog_prefers_connected_account_over_api_models(monkeypatch):
    credential_id = uuid.uuid4()
    monkeypatch.setattr(
        capabilities_module,
        "resolve_codex_executable",
        lambda: "/opt/codex/bin/codex",
    )
    monkeypatch.setattr(
        capabilities_module,
        "CodexAccountService",
        _ConnectedCodexAccount,
    )

    capabilities = await codex_capabilities(
        [{
            "id": credential_id,
            "name": "API model",
            "provider": "openai",
            "model_name": "gpt-api",
        }],
        tenant_id="tenant",
        user_id="user",
        auth_methods=["personal_api", "chatgpt"],
    )

    account_default_id = "codex:account:gpt-account-default"
    assert capabilities.default_model_id == account_default_id
    assert [model.id for model in capabilities.models if model.is_default] == [
        account_default_id
    ]
    api_model = next(
        model
        for model in capabilities.models
        if model.id == f"{CODEX_CREDENTIAL_PREFIX}{credential_id}"
    )
    assert api_model.is_default is False


@pytest.mark.asyncio
async def test_runtime_catalogs_filter_shared_credentials_by_compatibility(monkeypatch):
    openai_id = uuid.uuid4()
    anthropic_id = uuid.uuid4()
    rows = [
        {
            "id": openai_id,
            "name": "Shared OpenAI API",
            "provider": "openai",
            "runtime_scope": "langchain",
            "model_name": "gpt-shared",
        },
        {
            "id": anthropic_id,
            "name": "Anthropic API",
            "provider": "anthropic",
            "runtime_scope": "codex",
            "model_name": "claude-shared",
        },
    ]
    monkeypatch.setattr(capabilities_module.config.agent, "model", "")
    monkeypatch.setattr(capabilities_module.config.agent, "api_key", "")
    monkeypatch.setattr(
        capabilities_module,
        "resolve_codex_executable",
        lambda: "/opt/codex/bin/codex",
    )

    langchain = langchain_capabilities(rows)
    codex = await codex_capabilities(rows, auth_methods=["personal_api"])

    assert [model.id for model in langchain.models] == [
        f"{LANGCHAIN_CREDENTIAL_PREFIX}{openai_id}",
        f"{LANGCHAIN_CREDENTIAL_PREFIX}{anthropic_id}",
    ]
    assert [model.id for model in codex.models] == [
        f"{CODEX_CREDENTIAL_PREFIX}{openai_id}",
    ]

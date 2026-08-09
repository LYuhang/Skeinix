from __future__ import annotations

import uuid

import pytest
from vibecanvas_api.services.agent_runtime import capabilities as capabilities_module
from vibecanvas_api.services.agent_runtime.capabilities import (
    CODEX_CREDENTIAL_PREFIX,
    LANGCHAIN_CREDENTIAL_PREFIX,
    codex_capabilities,
    codex_credential_id,
    langchain_capabilities,
    langchain_credential_id,
    validate_model_effort,
)
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeCapabilities,
    RuntimeModelOption,
    RuntimeReasoningEffortOption,
    RuntimeType,
)


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
async def test_runtime_catalogs_never_share_saved_credentials(monkeypatch):
    langchain_id = uuid.uuid4()
    codex_id = uuid.uuid4()
    rows = [
        {
            "id": langchain_id,
            "name": "LangChain API",
            "provider": "openai",
            "runtime_scope": "langchain",
            "model_name": "langchain-model",
        },
        {
            "id": codex_id,
            "name": "Codex API",
            "provider": "openai",
            "runtime_scope": "codex",
            "model_name": "codex-model",
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
        f"{LANGCHAIN_CREDENTIAL_PREFIX}{langchain_id}",
    ]
    assert [model.id for model in codex.models] == [
        f"{CODEX_CREDENTIAL_PREFIX}{codex_id}",
    ]

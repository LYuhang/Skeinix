"""Runtime-owned model and reasoning-effort catalogs."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from vibecanvas_api.config import config
from vibecanvas_api.services.agent_runtime.codex_account import CodexAccountService
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeCapabilities,
    RuntimeModelOption,
    RuntimeReasoningEffortOption,
    RuntimeType,
)
from vibecanvas_api.services.codex_cli import resolve_codex_executable

LANGCHAIN_DEFAULT_MODEL_ID = "langchain:default"
LANGCHAIN_CREDENTIAL_PREFIX = "langchain:credential:"
CODEX_CREDENTIAL_PREFIX = "codex:credential:"
CODEX_ACCOUNT_MODEL_PREFIX = "codex:account:"
CODEX_MANAGED_MODEL_PREFIX = "codex:managed:"

# Codex's custom-provider contract is the OpenAI Responses wire protocol. Do
# not advertise credentials for providers whose native payload shape would be
# incompatible even though the host broker can serve them to LangChain.
_CODEX_RESPONSES_PROVIDERS = frozenset({"openai", "azure", "azure_openai"})


def runtime_model_connection_id(
    runtime_type: RuntimeType | str,
    model_id: str,
) -> str:
    """Return the deliberately small immutable Runtime variant for a model."""
    runtime = RuntimeType(runtime_type)
    if runtime == RuntimeType.LANGCHAIN:
        return "langchain"
    return (
        "codex:account"
        if model_id.startswith(CODEX_ACCOUNT_MODEL_PREFIX)
        else "codex:api"
    )

_OPENAI_REASONING_EFFORTS = (
    ("minimal", "Minimal", "Fastest supported reasoning mode."),
    ("low", "Low", "Lighter reasoning for straightforward tasks."),
    ("medium", "Medium", "Balanced reasoning depth and latency."),
    ("high", "High", "Deeper reasoning for complex tasks."),
    ("xhigh", "Extra high", "Maximum broadly supported reasoning depth."),
)


def _provider_from_model(model: str) -> str:
    provider, separator, _ = model.partition(":")
    return provider if separator else ""


def _model_name(model: str) -> str:
    _provider, separator, name = model.partition(":")
    return name if separator else model


def _normalized_provider(provider: str) -> str:
    return provider.strip().lower().replace("-", "_")


def _runtime_scope(row: Mapping[str, Any]) -> str:
    """Read a credential's Runtime owner with a safe legacy default."""
    return str(row.get("runtime_scope") or RuntimeType.LANGCHAIN.value).strip()


def _langchain_efforts(provider: str) -> list[RuntimeReasoningEffortOption]:
    normalized = provider.strip().lower().replace("-", "_")
    if normalized not in {"openai", "azure", "azure_openai"}:
        return []
    return [
        RuntimeReasoningEffortOption(id=value, label=label, description=description)
        for value, label, description in _OPENAI_REASONING_EFFORTS
    ]


def langchain_capabilities(
    credential_rows: Iterable[Mapping[str, Any]],
) -> RuntimeCapabilities:
    """Build the live LangChain catalog from explicit platform/user APIs."""
    platform_model = str(config.agent.model or "").strip()
    platform_provider = _provider_from_model(platform_model)
    models: list[RuntimeModelOption] = []
    if platform_model and str(config.agent.api_key or "").strip():
        models.append(RuntimeModelOption(
            id=LANGCHAIN_DEFAULT_MODEL_ID,
            label=platform_model,
            description="Operator-configured platform model and credential.",
            provider=platform_provider or None,
            is_default=True,
            supported_reasoning_efforts=_langchain_efforts(platform_provider),
            default_reasoning_effort=None,
        ))
    for row in credential_rows:
        if _runtime_scope(row) != RuntimeType.LANGCHAIN.value:
            continue
        credential_id = str(row.get("id") or "").strip()
        model_name = str(row.get("model_name") or "").strip()
        provider = str(row.get("provider") or "").strip()
        name = str(row.get("name") or model_name or provider or credential_id).strip()
        if not credential_id or not model_name:
            continue
        models.append(RuntimeModelOption(
            id=f"{LANGCHAIN_CREDENTIAL_PREFIX}{credential_id}",
            label=name,
            description=(
                f"{provider} · {model_name}" if provider else model_name
            ),
            provider=provider or None,
            supported_reasoning_efforts=_langchain_efforts(provider),
            default_reasoning_effort=None,
        ))
    if not models:
        return RuntimeCapabilities(
            runtime_type=RuntimeType.LANGCHAIN,
            runtime_available=True,
            authenticated=False,
            source="langchain.explicit_credentials",
            error_code="langchain_model_unavailable",
        )
    default = next((model.id for model in models if model.is_default), None)
    return RuntimeCapabilities(
        runtime_type=RuntimeType.LANGCHAIN,
        runtime_available=True,
        authenticated=True,
        source="langchain.explicit_credentials",
        models=models,
        default_model_id=default or models[0].id,
    )


def langchain_credential_id(model_id: str | None) -> uuid.UUID | None:
    """Resolve a public LangChain model-selection id to its credential id."""
    if model_id is None or model_id == LANGCHAIN_DEFAULT_MODEL_ID:
        return None
    if not model_id.startswith(LANGCHAIN_CREDENTIAL_PREFIX):
        raise ValueError("model_not_available_for_runtime")
    raw = model_id.removeprefix(LANGCHAIN_CREDENTIAL_PREFIX)
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValueError("model_not_available_for_runtime") from exc


async def codex_capabilities(
    credential_rows: Iterable[Mapping[str, Any]],
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    selected_managed_profile_id: str | None = None,
    auth_methods: Iterable[str] | None = None,
) -> RuntimeCapabilities:
    """Build the Codex catalog without exposing account credentials.

    API-backed requests use the host Runtime Model Broker and never expose a
    provider key. The optional ChatGPT mode contributes only model metadata;
    its official account cache is mounted later for an explicitly selected
    account request.
    """
    if resolve_codex_executable() is None:
        return RuntimeCapabilities(
            runtime_type=RuntimeType.CODEX,
            runtime_available=False,
            authenticated=None,
            source="codex.app-server+runtime-model-broker",
            error_code="codex_cli_unavailable",
        )

    allowed_auth = frozenset(
        auth_methods
        if auth_methods is not None
        else config.codex_runtime_auth_methods
    )
    models: list[RuntimeModelOption] = []
    for profile in (
        config.codex_managed_apis if "managed_api" in allowed_auth else ()
    ):
        profile_id = str(profile["id"])
        for index, model_name in enumerate(profile["models"]):
            models.append(RuntimeModelOption(
                id=(
                    f"{CODEX_MANAGED_MODEL_PREFIX}{profile_id}:{model_name}"
                ),
                label=f"{profile['name']} · {model_name}",
                description=(
                    "Operator-managed OpenAI API; connection details stay "
                    "on the host."
                ),
                provider="openai",
                is_default=(
                    profile_id == selected_managed_profile_id and index == 0
                ),
                supported_reasoning_efforts=_langchain_efforts("openai"),
                default_reasoning_effort=None,
            ))
    for row in credential_rows if "personal_api" in allowed_auth else ():
        if _runtime_scope(row) != RuntimeType.CODEX.value:
            continue
        credential_id = str(row.get("id") or "").strip()
        model_name = str(row.get("model_name") or "").strip()
        provider = _normalized_provider(str(row.get("provider") or ""))
        if (
            not credential_id
            or not model_name
            or provider not in _CODEX_RESPONSES_PROVIDERS
        ):
            continue
        name = str(row.get("name") or model_name).strip()
        models.append(RuntimeModelOption(
            id=f"{CODEX_CREDENTIAL_PREFIX}{credential_id}",
            label=name,
            description=f"{provider} · {model_name}",
            provider=provider,
            supported_reasoning_efforts=_langchain_efforts(provider),
            default_reasoning_effort=None,
        ))
    account_authenticated = False
    if "chatgpt" in allowed_auth and tenant_id and user_id:
        account_service = CodexAccountService(tenant_id, user_id)
        account_status = await account_service.status()
        account_authenticated = account_status.authenticated
        if account_authenticated:
            try:
                account_models = await account_service.list_models()
            except RuntimeError:
                account_models = []
            account_is_only_source = not models
            for account_model in account_models:
                models.append(account_model.model_copy(update={
                    "id": f"{CODEX_ACCOUNT_MODEL_PREFIX}{account_model.id}",
                    "is_default": (
                        account_is_only_source and account_model.is_default
                    ),
                    "provider": "chatgpt",
                    "description": (
                        account_model.description
                        or "Available through the connected ChatGPT account."
                    ),
                }))
    if not models:
        return RuntimeCapabilities(
            runtime_type=RuntimeType.CODEX,
            runtime_available=True,
            authenticated=False,
            source="codex.app-server+runtime-model-broker",
            error_code="codex_responses_model_unavailable",
        )
    default = next((model.id for model in models if model.is_default), None)
    return RuntimeCapabilities(
        runtime_type=RuntimeType.CODEX,
        runtime_available=True,
        authenticated=True,
        source="codex.app-server+runtime-model-broker",
        models=models,
        default_model_id=default or (models[0].id if models else None),
    )


def codex_credential_id(model_id: str | None) -> uuid.UUID | None:
    """Resolve a public Codex broker model id to its saved credential."""
    if model_id is None:
        return None
    if not model_id.startswith(CODEX_CREDENTIAL_PREFIX):
        raise ValueError("model_not_available_for_runtime")
    raw = model_id.removeprefix(CODEX_CREDENTIAL_PREFIX)
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValueError("model_not_available_for_runtime") from exc


def codex_account_model_id(model_id: str | None) -> str | None:
    if model_id is None or not model_id.startswith(CODEX_ACCOUNT_MODEL_PREFIX):
        return None
    value = model_id.removeprefix(CODEX_ACCOUNT_MODEL_PREFIX).strip()
    if not value or len(value) > 512:
        raise ValueError("model_not_available_for_runtime")
    return value


def codex_managed_model(model_id: str | None) -> tuple[str, str] | None:
    if model_id is None or not model_id.startswith(CODEX_MANAGED_MODEL_PREFIX):
        return None
    value = model_id.removeprefix(CODEX_MANAGED_MODEL_PREFIX)
    profile_id, separator, model = value.partition(":")
    if not separator or not profile_id or not model or len(model) > 512:
        raise ValueError("model_not_available_for_runtime")
    return profile_id, model


def validate_model_effort(
    capabilities: RuntimeCapabilities,
    *,
    model_id: str | None,
    reasoning_effort: str | None,
) -> RuntimeModelOption | None:
    """Validate a per-turn selection against the same catalog the UI renders."""
    effective_model_id = model_id or capabilities.default_model_id
    model = next(
        (option for option in capabilities.models if option.id == effective_model_id),
        None,
    )
    if model is None:
        raise ValueError("model_not_available_for_runtime")
    if reasoning_effort is not None:
        allowed = {option.id for option in model.supported_reasoning_efforts}
        if reasoning_effort not in allowed:
            raise ValueError("reasoning_effort_not_supported_by_model")
    return model

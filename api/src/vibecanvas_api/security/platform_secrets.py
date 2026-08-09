"""Just-in-time platform credential resolution through a managed secret store."""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Protocol


class PlatformSecretError(RuntimeError):
    """Messages intentionally exclude secret ids, values, and provider output."""


class PlatformSecretResolver(Protocol):
    def resolve(self, secret_id: str) -> str: ...

    async def resolve_async(self, secret_id: str) -> str: ...


class AwsSecretsManagerResolver:
    """AWS Secrets Manager client using the workload identity chain.

    The client is reusable, but plaintext values are never cached. Each use is
    resolved just in time and remains only in the caller's local stack.
    """

    def __init__(self, client=None):
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - packaging gate
                raise PlatformSecretError(
                    "managed secret client is not installed"
                ) from exc
            client = boto3.client("secretsmanager")
        self._client = client

    def resolve(self, secret_id: str) -> str:
        if not secret_id:
            raise PlatformSecretError("managed secret reference is missing")
        try:
            response = self._client.get_secret_value(SecretId=secret_id)
        except Exception as exc:
            raise PlatformSecretError("managed secret resolution failed") from exc
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise PlatformSecretError("managed secret value is unavailable")
        return value

    async def resolve_async(self, secret_id: str) -> str:
        return await asyncio.to_thread(self.resolve, secret_id)


@lru_cache(maxsize=1)
def platform_secret_resolver() -> PlatformSecretResolver:
    return AwsSecretsManagerResolver()

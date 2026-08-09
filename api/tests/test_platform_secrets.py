from __future__ import annotations

import pytest

from vibecanvas_api.security.platform_secrets import (
    AwsSecretsManagerResolver,
    PlatformSecretError,
)


class _Client:
    def __init__(self, value=None, error: Exception | None = None):
        self.value = value
        self.error = error
        self.requests: list[str] = []

    def get_secret_value(self, *, SecretId: str):
        self.requests.append(SecretId)
        if self.error:
            raise self.error
        return {"SecretString": self.value}


def test_platform_secret_is_resolved_without_value_cache() -> None:
    client = _Client("smtp-password")
    resolver = AwsSecretsManagerResolver(client)
    assert resolver.resolve("prod/smtp") == "smtp-password"
    client.value = "rotated-password"
    assert resolver.resolve("prod/smtp") == "rotated-password"
    assert client.requests == ["prod/smtp", "prod/smtp"]


def test_platform_secret_errors_never_include_provider_details() -> None:
    resolver = AwsSecretsManagerResolver(
        _Client(error=RuntimeError("provider leaked sensitive detail"))
    )
    with pytest.raises(PlatformSecretError) as caught:
        resolver.resolve("prod/smtp")
    assert "sensitive" not in str(caught.value)
    assert "prod/smtp" not in str(caught.value)

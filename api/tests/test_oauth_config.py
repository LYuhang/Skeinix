import pytest

from vibecanvas_api.config import PublicUrlsConfig


def test_public_url_preserves_reverse_proxy_prefix(monkeypatch):
    monkeypatch.setenv("VIBECANVAS_PUBLIC_URL", "https://workspace.example.com/vibecanvas/")

    urls = PublicUrlsConfig({})

    assert urls.absolute("api/v1/mcp-servers/oauth/callback") == (
        "https://workspace.example.com/vibecanvas/api/v1/mcp-servers/oauth/callback"
    )


def test_public_url_rejects_relative_values(monkeypatch):
    monkeypatch.setenv("VIBECANVAS_PUBLIC_URL", "workspace.example.com")

    with pytest.raises(ValueError, match="absolute HTTP"):
        PublicUrlsConfig({})

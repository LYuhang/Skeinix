import asyncio

import httpx
import pytest

from vibecanvas_api.services import mcp_catalog
from vibecanvas_api.services.mcp_catalog import (
    normalize_official_entry,
    normalize_smithery_detail,
    normalize_smithery_entry,
)


@pytest.fixture(autouse=True)
def clear_catalog_caches():
    mcp_catalog._cache.clear()
    mcp_catalog._detail_cache.clear()
    yield
    mcp_catalog._cache.clear()
    mcp_catalog._detail_cache.clear()


def test_official_remote_entry_is_installable_and_exposes_only_declared_config():
    item = normalize_official_entry({
        "server": {
            "name": "io.github.example/search-mcp",
            "title": "Search MCP",
            "description": "Search external sources.",
            "version": "1.2.3",
            "remotes": [{
                "type": "streamable-http",
                "url": "https://example.test/mcp",
                "headers": [{
                    "name": "Authorization",
                    "value": "Bearer {search_api_key}",
                    "description": "Search API key",
                    "isRequired": True,
                    "isSecret": True,
                }],
            }],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "publishedAt": "2026-07-01T00:00:00Z",
            },
        },
    })

    assert item["source_id"] == "io.github.example/search-mcp"
    assert item["connection"] == {
        "transport": "streamable_http",
        "endpoint": "https://example.test/mcp",
        "connection_config": {"url": "https://example.test/mcp"},
    }
    assert item["config_fields"] == [{
        "key": "search_api_key",
        "label": "Search Api Key",
        "description": "Search API key",
        "required": True,
        "secret": True,
        "target": "bearer",
        "input_type": "string",
        "choices": [],
        "default": None,
        "placeholder": "",
    }]


def test_official_npm_entry_generates_stdio_config_and_env_fields():
    item = normalize_official_entry({
        "server": {
            "name": "io.github.example/files",
            "version": "2.0.0",
            "packages": [{
                "registryType": "npm",
                "identifier": "@example/files-mcp",
                "version": "2.0.0",
                "runtimeHint": "npx",
                "environmentVariables": [{
                    "name": "FILES_TOKEN",
                    "isRequired": True,
                    "isSecret": True,
                }],
            }],
        },
    })

    assert item["connection"]["transport"] == "stdio"
    assert item["connection"]["endpoint"] == "npx"
    assert item["connection"]["connection_config"]["args"] == [
        "-y",
        "@example/files-mcp@2.0.0",
    ]
    assert item["config_fields"][0]["target"] == "env:FILES_TOKEN"


def test_smithery_search_and_detail_keep_popularity_and_resolve_endpoint():
    search_item = normalize_smithery_entry({
        "qualifiedName": "exa",
        "displayName": "Exa Search",
        "description": "Search the web.",
        "verified": True,
        "useCount": 1234,
    })
    detail_item = normalize_smithery_detail({
        "qualifiedName": "exa",
        "displayName": "Exa Search",
        "description": "Search the web.",
        "verified": True,
        "useCount": 1234,
        "connections": [{
            "type": "http",
            "deploymentUrl": "https://exa.run.tools",
            "configSchema": {},
        }],
    })

    assert search_item["usage_count"] == 1234
    assert search_item["connection"] is None
    assert detail_item["connection"] == {
        "transport": "streamable_http",
        "endpoint": "https://exa.run.tools",
        "connection_config": {"url": "https://exa.run.tools"},
    }


def test_official_remote_variables_preserve_declared_ui_metadata():
    item = normalize_official_entry({
        "server": {
            "name": "com.example/regions",
            "remotes": [{
                "type": "streamable-http",
                "url": "https://api.example.test/{region}/mcp",
                "variables": {
                    "region": {
                        "description": "Deployment region",
                        "isRequired": True,
                        "choices": ["us-east-1", "eu-west-1"],
                        "default": "us-east-1",
                    },
                },
            }],
        },
    })

    assert item["config_fields"] == [{
        "key": "region",
        "label": "Region",
        "description": "Deployment region",
        "required": True,
        "secret": False,
        "target": "url_variable:region",
        "input_type": "string",
        "choices": ["us-east-1", "eu-west-1"],
        "default": "us-east-1",
        "placeholder": "",
    }]


def test_smithery_schema_maps_header_and_query_fields():
    item = normalize_smithery_detail({
        "qualifiedName": "browserbase",
        "deploymentUrl": "https://browserbase.run.tools",
        "connections": [{
            "type": "http",
            "deploymentUrl": "https://browserbase.run.tools",
            "configSchema": {
                "type": "object",
                "required": ["apiKey"],
                "properties": {
                    "apiKey": {
                        "type": "string",
                        "title": "API Key",
                        "description": "Browserbase API key",
                        "x-from": {"header": "browserbase-api-key"},
                    },
                    "region": {
                        "type": "string",
                        "enum": ["us", "eu"],
                        "default": "us",
                        "x-from": {"query": "region"},
                    },
                },
            },
        }],
    })

    assert item["auth_mode"] == "configuration"
    assert item["config_fields"][0]["target"] == "header:browserbase-api-key"
    assert item["config_fields"][0]["secret"] is True
    assert item["config_fields"][1]["target"] == "query:region"
    assert item["config_fields"][1]["choices"] == ["us", "eu"]


@pytest.mark.asyncio
async def test_catalog_uses_explicit_control_plane_proxy(monkeypatch):
    request_arguments = {}

    async def fake_request(method, url, **kwargs):
        request_arguments.update({"method": method, "url": url, **kwargs})
        return httpx.Response(
            200,
            json={"servers": []},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(mcp_catalog, "request_pinned_public_url", fake_request)
    monkeypatch.setattr(
        mcp_catalog.config,
        "control_plane_http_proxy",
        "http://proxy.internal:7897",
    )

    payload = await mcp_catalog._fetch_json(mcp_catalog._OFFICIAL_URL)

    assert payload == {"servers": []}
    assert request_arguments["proxy"] == "http://proxy.internal:7897"


@pytest.mark.asyncio
async def test_catalog_detail_is_cached_after_first_resolution(monkeypatch):
    fetches = 0

    async def fake_fetch_json(url, *, params=None):
        nonlocal fetches
        fetches += 1
        return {
            "server": {
                "name": "io.github.example/cached",
                "title": "Cached MCP",
                "packages": [{"registryType": "npm", "identifier": "cached-mcp"}],
            },
        }

    monkeypatch.setattr(mcp_catalog, "_fetch_json", fake_fetch_json)

    first = await mcp_catalog.resolve_catalog_item(
        source="official", source_id="io.github.example/cached",
    )
    second = await mcp_catalog.resolve_catalog_item(
        source="official", source_id="io.github.example/cached",
    )

    assert first == second
    assert fetches == 1


@pytest.mark.asyncio
async def test_remote_auth_discovery_has_an_overall_deadline(monkeypatch):
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(mcp_catalog._REMOTE_AUTH_DISCOVERY_TIMEOUT_S + 1)

    monkeypatch.setattr(mcp_catalog, "request_pinned_public_url", never_returns)
    monkeypatch.setattr(mcp_catalog, "_REMOTE_AUTH_DISCOVERY_TIMEOUT_S", 0.01)
    item = {
        "connection": {
            "transport": "streamable_http",
            "endpoint": "https://example.test/mcp",
        },
        "config_fields": [],
        "auth_mode": "connection_discovery",
    }

    resolved = await mcp_catalog._discover_remote_auth(item)

    assert resolved["auth_mode"] == "connection_discovery"

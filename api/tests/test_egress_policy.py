# -*- coding: utf-8 -*-
"""Plan B B6 — per-workflow sandbox egress allowlist computation.

Focus is the PURE core (:func:`compute_allow_hosts_from_parts` + ``_host_of``)
which has no DB dependency. Two light integration checks cover the sync wrapper
(credentials mocked) and the runner threading a non-None
``allow_hosts`` into a stubbed ``provider.run_workflow``.
"""
from __future__ import annotations

from vibecanvas_api.services.sandbox.egress_policy import (
    _host_of,
    _workflow_declared_http_hosts,
    compute_allow_hosts,
    compute_allow_hosts_from_parts,
)


# ---------------------------------------------------------------------------
# pure core
# ---------------------------------------------------------------------------
def test_union_of_llm_mcp_user():
    creds = {"m1": {"api_url": "https://llm.test/v1", "proxy": None}}
    mcp = ["https://mcp.test/sse"]
    user = ["api.example.com", "https://b.example.com/x"]
    out = compute_allow_hosts_from_parts(creds, mcp, user, [])
    assert out == {"llm.test", "mcp.test", "api.example.com", "b.example.com"}


def test_proxy_and_builtin_included():
    creds = {
        "m1": {"api_url": "https://llm.test/v1", "proxy": "http://proxy.test:8080"},
    }
    out = compute_allow_hosts_from_parts(
        creds, [], [], ["https://platform.test/v1"])
    assert "proxy.test" in out
    assert "platform.test" in out
    assert "llm.test" in out


def test_host_parsing():
    assert _host_of("https://h.test:443/p") == "h.test"
    assert _host_of("h.test") == "h.test"
    assert _host_of("h.test:80") == "h.test"
    assert _host_of("") is None
    assert _host_of(None) is None
    assert _host_of("HTTPS://H.TEST/p") == "h.test"
    assert _host_of("H.TEST") == "h.test"


def test_empty_inputs():
    assert compute_allow_hosts_from_parts({}, [], [], []) == set()
    assert compute_allow_hosts_from_parts(None, None, None, None) == set()


def test_blank_api_url_uses_provider_default_host():
    """A saved credential with ``provider`` set but ``api_url`` empty/None
    resolves (in the engine) to the provider's HARDCODED default host. The
    allowlist must include that host or the legit LLM call is blocked in prod
    proxy mode. Default hosts match the real SDK / engine defaults:
      * openai    → api.openai.com (engine CANONICAL_PROVIDERS default_url
                    https://api.openai.com/v1)
      * anthropic → api.anthropic.com (Anthropic SDK default base_url; engine
                    default_url is "" because the SDK supplies it)
      * google_genai/gemini → generativelanguage.googleapis.com (google.genai
                    Gemini-API host; engine default_url is "")
    """
    creds = {
        "o": {"provider": "openai", "model_name": "gpt-4o", "api_url": None},
        "a": {"provider": "anthropic", "model_name": "claude", "api_url": None},
        "g": {"provider": "google_genai", "model_name": "gemini-2.0", "api_url": ""},
        "gem": {"provider": "Gemini", "model_name": "gemini-2.0", "api_url": None},
    }
    out = compute_allow_hosts_from_parts(creds, [], [], [])
    assert "api.openai.com" in out
    assert "api.anthropic.com" in out
    assert "generativelanguage.googleapis.com" in out


def test_api_url_wins_over_provider_default():
    """An entry WITH api_url uses api_url (NOT the provider default)."""
    creds = {
        "o": {"provider": "openai", "model_name": "gpt-4o",
              "api_url": "https://gateway.example.test/v1"},
    }
    out = compute_allow_hosts_from_parts(creds, [], [], [])
    assert "gateway.example.test" in out
    assert "api.openai.com" not in out


def test_azure_blank_api_url_no_default_host():
    """Azure has no platform-wide default host (per-resource endpoint) → a blank
    api_url contributes NO derivable host (and must not crash)."""
    creds = {"az": {"provider": "azure_openai", "model_name": "dep", "api_url": None}}
    out = compute_allow_hosts_from_parts(creds, [], [], [])
    assert out == set()


def test_unknown_provider_blank_api_url_no_default_host():
    """An unknown provider with blank api_url contributes nothing (no crash)."""
    creds = {"x": {"provider": "mystery", "model_name": "m", "api_url": None}}
    assert compute_allow_hosts_from_parts(creds, [], [], []) == set()


def test_unparseable_entries_dropped():
    # Bad / empty entries don't blow up and don't open everything.
    creds = {"m1": {"api_url": None, "proxy": ""}, "bad": "not-a-dict"}
    out = compute_allow_hosts_from_parts(creds, [None, ""], ["", None], [None])
    assert out == set()


def test_static_http_request_hosts_are_inferred_but_dynamic_authorities_are_not():
    workflow = {
        "node_1": {
            "node_type": "HTTPRequestNode",
            "node_config": {"url": "https://HTTPBIN.org/get?value={{value}}"},
        },
        "node_2": {
            "node_type": "HTTPRequestNode",
            "node_config": {"url": "https://{{tenant_host}}/v1"},
        },
        "node_3": {
            "node_type": "CodeNode",
            "node_config": {"url": "https://ignored.example"},
        },
    }

    assert _workflow_declared_http_hosts(workflow) == ["httpbin.org"]


# ---------------------------------------------------------------------------
# sync wrapper integration (credentials mocked)
# ---------------------------------------------------------------------------
def _prompt_node(model_name: str) -> dict:
    return {
        "node_id": "node_2",
        "node_name": "p",
        "node_type": "PromptNode",
        "node_config": {"model_name": model_name},
        "children": [],
    }


def test_sync_wrapper_unions_creds_user_hosts(monkeypatch):
    import vibecanvas_api.services.sandbox.egress_policy as ep

    wf = {
        "__meta__": {
            "settings": {
                "egress": {"allowed_hosts": ["user.example.com"]},
            }
        },
        "node_2": _prompt_node("MySavedModel"),
    }

    # The wrapper consumes the exact execution-scoped broker mapping that will
    # be staged for the sandbox; it must not resolve credentials or mint a
    # second capability while computing egress policy.
    monkeypatch.setattr(ep, "collect_referenced_credential_names",
                        lambda wf: {"MySavedModel"})
    monkeypatch.setattr(ep, "_builtin_model_names", lambda: set())

    out = compute_allow_hosts(
        wf,
        user_id="user-1",
        creds_mapping={
            "MySavedModel": {
                "api_url": "https://saved.test/v1",
                "proxy": None,
            }
        },
    )
    assert out == {"saved.test", "user.example.com"}

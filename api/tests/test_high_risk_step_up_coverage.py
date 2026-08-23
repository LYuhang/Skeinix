from __future__ import annotations

import inspect

from vibecanvas_api.auth.deps import require_recent_step_up
from vibecanvas_api.routes import (
    auth,
    deployments,
    kb,
    llm_credentials,
    mcp_servers,
    organizations,
    tasks,
    webauthn,
    workflows,
)


DIRECT_STEP_UP_ENDPOINTS = (
    auth.delete_account,
    organizations.update_service_account_status,
    organizations.rotate_service_account_generation,
    organizations.update_organization_member,
    organizations.create_group,
    organizations.update_group,
    organizations.archive_group,
    organizations.set_group_member,
    organizations.revoke_group_member,
    llm_credentials.create_credential,
    llm_credentials.delete_credential,
    mcp_servers.create_mcp_server,
    mcp_servers.start_mcp_oauth_connection,
    mcp_servers.disconnect_mcp_oauth_connection,
    mcp_servers.delete_mcp_server,
    deployments.create_deployment,
    deployments.rotate_key,
    deployments.grant_deployment_access,
    deployments.revoke_deployment_access,
    workflows.grant_workflow_access,
    workflows.revoke_workflow_access,
    tasks.grant_task_access,
    tasks.revoke_task_access,
    kb.grant_kb_access,
    kb.revoke_kb_access,
    webauthn.delete_credential,
)


BUSINESS_CAPABILITY_ENDPOINTS = (
    workflows.create_workflow,
    workflows.update_workflow_meta,
    workflows.apply_edits,
    workflows.commit_workflow,
    workflows.check_workflow,
    workflows.submit_batch,
    mcp_servers.refresh_mcp_server,
    tasks.run_scheduled_now,
    kb.search,
)


def _has_direct_step_up(endpoint) -> bool:
    for parameter in inspect.signature(endpoint).parameters.values():
        default = parameter.default
        if getattr(default, "dependency", None) is require_recent_step_up:
            return True
    return False


def test_security_boundary_mutations_require_phishing_resistant_step_up() -> None:
    missing = [
        endpoint.__name__
        for endpoint in DIRECT_STEP_UP_ENDPOINTS
        if not _has_direct_step_up(endpoint)
    ]
    assert missing == []


def test_sensitive_partial_updates_gate_only_secret_or_destination_changes() -> None:
    llm_source = inspect.getsource(llm_credentials.update_credential)
    for field in {"api_key", "api_url", "proxy", "provider", "runtime_scope"}:
        assert f'"{field}"' in llm_source
    assert "await require_recent_step_up(ctx)" in llm_source

    mcp_source = inspect.getsource(mcp_servers.patch_mcp_server)
    assert '{"endpoint", "connection_config", "auth_config"}' in mcp_source
    assert "await require_recent_step_up(ctx)" in mcp_source


def test_step_up_does_not_gate_ordinary_product_capabilities() -> None:
    unexpectedly_gated = [
        endpoint.__name__
        for endpoint in BUSINESS_CAPABILITY_ENDPOINTS
        if _has_direct_step_up(endpoint)
    ]
    assert unexpectedly_gated == []

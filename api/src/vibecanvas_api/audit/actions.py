"""Security audit action taxonomy mirrored by the database CHECK constraint.

This is the Python source of truth mirrored by the ``ck_audit_action`` CHECK
in Alembic migrations 009 and 066 — the effective lists MUST stay identical.
"""
from __future__ import annotations

AUTH_LOGIN_SUCCESS = "auth.login_success"
AUTH_LOGIN_FAILURE = "auth.login_failure"
AUTH_LOGOUT = "auth.logout"
AUTH_REGISTER = "auth.register"
AUTH_PASSWORD_RESET_REQUEST = "auth.password_reset_request"
AUTH_PASSWORD_RESET_COMPLETE = "auth.password_reset_complete"
AUTH_SESSION_LIST = "auth.session_list"
AUTH_SESSION_REVOKE = "auth.session_revoke"
AUTH_SESSION_ROTATE = "auth.session_rotate"
AUTH_ACCOUNT_DELETE_REQUEST = "auth.account_delete_request"
AUTH_ACCOUNT_DELETE_CANCEL = "auth.account_delete_cancel"
AUTH_PASSKEY_REGISTER = "auth.passkey_register"
AUTH_PASSKEY_VERIFY = "auth.passkey_verify"
AUTH_PASSKEY_REMOVE = "auth.passkey_remove"
AUTH_SSO_LOGIN_SUCCESS = "auth.sso_login_success"
AUTH_SSO_LOGIN_FAILURE = "auth.sso_login_failure"

DEPLOYMENT_KEY_ROTATE = "deployment.key_rotate"
MCP_SERVER_CREDENTIAL_CHANGE = "mcp_server.credential_change"
LLM_CREDENTIAL_CONNECTION_CHANGE = "llm_credential.connection_change"

DEPLOYMENT_CREATE = "deployment.create"
DEPLOYMENT_DELETE = "deployment.delete"
MCP_SERVER_CREATE = "mcp_server.create"
MCP_SERVER_DELETE = "mcp_server.delete"
WORKFLOW_DELETE = "workflow.delete"
KB_DELETE = "kb.delete"

ORGANIZATION_CREATE = "organization.create"
ORGANIZATION_UPDATE = "organization.update"
ORGANIZATION_MEMBER_CHANGE = "organization.member_change"
SHARE_LOOKUP = "share.lookup"
SHARE_GRANT = "share.grant"
SHARE_REVOKE = "share.revoke"
SERVICE_ACCOUNT_CREATE = "service_account.create"
SERVICE_ACCOUNT_STATUS_CHANGE = "service_account.status_change"
SECRET_CREATE = "secret.create"
SECRET_DESTROY = "secret.destroy"
PURGE_STARTED = "purge.started"
PURGE_COMPLETED = "purge.completed"
PURGE_FAILED = "purge.failed"
PRIVILEGED_ACCESS_REQUEST = "privileged_access.request"
PRIVILEGED_ACCESS_APPROVE = "privileged_access.approve"
PRIVILEGED_ACCESS_DENY = "privileged_access.deny"
PRIVILEGED_ACCESS_ACTIVATE = "privileged_access.activate"
PRIVILEGED_ACCESS_NOTIFY_OWNER = "privileged_access.notify_owner"
PRIVILEGED_ACCESS_USE = "privileged_access.use"
PRIVILEGED_ACCESS_REVOKE = "privileged_access.revoke"
PRIVILEGED_ELIGIBILITY_CHANGE = "privileged_access.eligibility_change"
PRIVILEGED_ELIGIBILITY_REVIEW = "privileged_access.eligibility_review"
ENTERPRISE_IDENTITY_CONFIG_CHANGE = "enterprise_identity.config_change"
ENTERPRISE_IDENTITY_SCIM_SYNC = "enterprise_identity.scim_sync"

AUDIT_ACTIONS: frozenset[str] = frozenset({
    AUTH_LOGIN_SUCCESS, AUTH_LOGIN_FAILURE, AUTH_LOGOUT, AUTH_REGISTER,
    AUTH_PASSWORD_RESET_REQUEST, AUTH_PASSWORD_RESET_COMPLETE,
    AUTH_SESSION_LIST, AUTH_SESSION_REVOKE, AUTH_SESSION_ROTATE,
    AUTH_ACCOUNT_DELETE_REQUEST, AUTH_ACCOUNT_DELETE_CANCEL,
    AUTH_PASSKEY_REGISTER, AUTH_PASSKEY_VERIFY, AUTH_PASSKEY_REMOVE,
    AUTH_SSO_LOGIN_SUCCESS, AUTH_SSO_LOGIN_FAILURE,
    DEPLOYMENT_KEY_ROTATE, MCP_SERVER_CREDENTIAL_CHANGE,
    LLM_CREDENTIAL_CONNECTION_CHANGE,
    DEPLOYMENT_CREATE, DEPLOYMENT_DELETE, MCP_SERVER_CREATE, MCP_SERVER_DELETE,
    WORKFLOW_DELETE, KB_DELETE,
    ORGANIZATION_CREATE, ORGANIZATION_UPDATE, ORGANIZATION_MEMBER_CHANGE,
    SHARE_LOOKUP, SHARE_GRANT, SHARE_REVOKE, SERVICE_ACCOUNT_CREATE,
    SERVICE_ACCOUNT_STATUS_CHANGE, SECRET_CREATE, SECRET_DESTROY,
    PURGE_STARTED, PURGE_COMPLETED, PURGE_FAILED,
    PRIVILEGED_ACCESS_REQUEST, PRIVILEGED_ACCESS_APPROVE,
    PRIVILEGED_ACCESS_DENY, PRIVILEGED_ACCESS_ACTIVATE,
    PRIVILEGED_ACCESS_NOTIFY_OWNER, PRIVILEGED_ACCESS_USE,
    PRIVILEGED_ACCESS_REVOKE,
    PRIVILEGED_ELIGIBILITY_CHANGE, PRIVILEGED_ELIGIBILITY_REVIEW,
    ENTERPRISE_IDENTITY_CONFIG_CHANGE, ENTERPRISE_IDENTITY_SCIM_SYNC,
})

# target_type values
TARGET_WORKFLOW = "workflow"
TARGET_DEPLOYMENT = "deployment"
TARGET_KB = "kb"
TARGET_MCP_SERVER = "mcp_server"
TARGET_LLM_CREDENTIAL = "llm_credential"
TARGET_SESSION = "session"
TARGET_ORGANIZATION = "organization"
TARGET_SHARE = "share"
TARGET_SERVICE_ACCOUNT = "service_account"
TARGET_SECRET = "secret"
TARGET_PURGE_JOB = "purge_job"
TARGET_PRIVILEGED_ACCESS = "privileged_access"
TARGET_PRIVILEGED_ELIGIBILITY = "privileged_eligibility"
TARGET_ENTERPRISE_IDENTITY = "enterprise_identity"

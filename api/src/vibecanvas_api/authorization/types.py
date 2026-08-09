"""Stable authorization value types shared by HTTP, workers, and MCP."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PrincipalType(StrEnum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"


class RelationshipSubjectType(StrEnum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    GROUP = "group"
    ORGANIZATION = "organization"


class ResourceType(StrEnum):
    IDENTITY = "identity"
    PLATFORM_CATALOG = "platform_catalog"
    ORGANIZATION = "organization"
    GROUP = "group"
    CHAT = "chat"
    CHAT_MESSAGE = "chat_message"
    AGENT_RUN = "agent_run"
    AGENT_PLAN = "agent_plan"
    HITL_REQUEST = "hitl_request"
    INTERACTIVE_ARTIFACT = "interactive_artifact"
    BACKGROUND_JOB = "background_job"
    BROWSER_BINDING = "browser_binding"
    RUNTIME_STATE = "runtime_state"
    VFS_RUN = "vfs_run"
    VFS_PATH = "vfs_path"
    WORKFLOW = "workflow"
    WORKFLOW_VERSION = "workflow_version"
    WORKFLOW_EXECUTION = "workflow_execution"
    TEMPLATE = "template"
    TASK = "task"
    TASK_EXECUTION = "task_execution"
    DEPLOYMENT = "deployment"
    DEPLOYMENT_INVOCATION = "deployment_invocation"
    STORAGE_ROOT = "storage_root"
    KNOWLEDGE_BASE = "knowledge_base"
    KNOWLEDGE_BASE_FILE = "knowledge_base_file"
    MCP_INSTALLATION = "mcp_installation"
    MCP_OAUTH_CONNECTION = "mcp_oauth_connection"
    SKILL_INSTALLATION = "skill_installation"
    SKILL_REVISION = "skill_revision"
    LLM_CREDENTIAL = "llm_credential"
    SERVICE_ACCOUNT = "service_account"


class Action(StrEnum):
    VIEW_METADATA = "view_metadata"
    VIEW = "view"
    EXPORT = "export"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    TRANSFER = "transfer"
    MANAGE_ACCESS = "manage_access"
    USE = "use"
    EXECUTE = "execute"
    CANCEL = "cancel"
    RESUME = "resume"
    INSPECT_RUNS = "inspect_runs"
    DEPLOY = "deploy"
    MOUNT = "mount"
    PUBLISH = "publish"
    MANAGE_SECRET = "manage_secret"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_POLICY = "manage_policy"
    VIEW_AUDIT = "view_audit"


class ConsistencyPreference(StrEnum):
    MINIMIZE_LATENCY = "MINIMIZE_LATENCY"
    HIGHER_CONSISTENCY = "HIGHER_CONSISTENCY"


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    type: PrincipalType
    id: str


@dataclass(frozen=True, slots=True)
class ResourceRef:
    type: ResourceType
    id: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class AuthzRequestContext:
    active_organization_id: str
    request_id: str = ""
    session_id: str = ""
    session_generation: int = 0
    membership_id: str = ""
    membership_role: str = ""
    membership_status: str = ""
    authentication_strength: str = ""
    authz_generation: int = 0
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    )


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    capabilities: frozenset[Action] = frozenset()
    effective_role: str | None = None
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class AuthorizedResource:
    resource: ResourceRef
    decision: Decision


@dataclass(frozen=True, slots=True)
class AuthorizationCheck:
    principal: PrincipalRef
    action: Action
    resource: ResourceRef
    context: AuthzRequestContext
    consistency: ConsistencyPreference = ConsistencyPreference.MINIMIZE_LATENCY


@dataclass(frozen=True, slots=True)
class RelationshipSubject:
    type: RelationshipSubjectType
    id: str
    relation: str | None = None

    def to_openfga(self) -> str:
        value = f"{self.type.value}:{self.id}"
        return f"{value}#{self.relation}" if self.relation else value


@dataclass(frozen=True, slots=True)
class RelationshipBinding:
    subject: RelationshipSubject
    relation: str
    resource: ResourceRef


@dataclass(frozen=True, slots=True)
class BindingPage:
    bindings: tuple[RelationshipBinding, ...]
    continuation_token: str = ""

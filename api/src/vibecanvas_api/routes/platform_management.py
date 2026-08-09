"""Privacy-preserving platform operations overview.

The control plane exposes aggregate infrastructure and identity-lifecycle
metadata only. It never returns chat text, file names/content, prompts,
credentials, runtime state, or decrypted user profiles.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func, select

from vibecanvas_api.auth.deps import AuthContext, current_user
from vibecanvas_api.auth.privileged_access import platform_role_for_user
from vibecanvas_api.services.sandbox.manager import get_sandbox_manager
from vibecanvas_api.audit.actions import AUDIT_ACTIONS
from vibecanvas_api.storage.models import AuditLog, Session, User
from vibecanvas_api.storage.models_org import Organization, OrgMembership
from vibecanvas_api.storage.sync_session import short_admin_session


router = APIRouter(prefix="/api/v1/platform-management", tags=["platform-management"])


# Each action has exactly one primary category. Keeping this projection next to
# the platform read model avoids exposing encrypted/private audit payloads just
# to draw an operations dashboard.
AUDIT_CATEGORY_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity", ("auth.", "organization.", "enterprise_identity.")),
    (
        "access_security",
        (
            "share.",
            "service_account.",
            "secret.",
            "privileged_access.",
            "deployment.key_rotate",
            "mcp_server.credential_change",
        ),
    ),
    ("resources", ("deployment.", "mcp_server.", "workflow.", "kb.")),
    ("data_lifecycle", ("purge.",)),
)

AUDIT_CATEGORY_ORDER = (
    "identity",
    "access_security",
    "resources",
    "data_lifecycle",
    "runtime_operations",
)


def _audit_category(action: str) -> str:
    for category, prefixes in AUDIT_CATEGORY_PREFIXES:
        if action.startswith(prefixes):
            return category
    return "runtime_operations"


def _audit_catalog() -> list[dict]:
    actions_by_category = {category: [] for category in AUDIT_CATEGORY_ORDER}
    for action in sorted(AUDIT_ACTIONS):
        actions_by_category[_audit_category(action)].append(action)
    # Runtime execution is observed by operational telemetry today. It is
    # deliberately listed as a coverage gap instead of pretending that an
    # immutable audit event exists when it does not.
    missing_by_category = {
        "identity": [
            "group",
            "group_membership",
            "organization_policy",
            "webauthn_credential",
        ],
        "access_security": [
            "llm_credential",
            "mcp_oauth_connection",
            "resource_acl",
            "organization_role",
        ],
        "resources": [
            "task",
            "template",
            "skill",
            "knowledge_base_file",
            "mcp_installation",
        ],
        "data_lifecycle": [
            "vfs_path",
            "chat",
            "chat_message",
            "retention_policy",
            "export_job",
        ],
        "runtime_operations": [
            "agent_run",
            "agent_plan",
            "hitl_request",
            "interactive_artifact",
            "background_task",
            "sandbox",
            "workflow_execution",
            "task_execution",
            "deployment_invocation",
            "browser_binding",
        ],
    }
    return [
        {
            "category": category,
            "actions": actions_by_category[category],
            "missing_objects": missing_by_category[category],
            "coverage": "complete" if not missing_by_category[category] else "partial",
        }
        for category in AUDIT_CATEGORY_ORDER
    ]


async def _platform_role(ctx: AuthContext) -> str:
    async with short_admin_session() as session:
        role = await platform_role_for_user(session, ctx.user_id)
    if role is None:
        # Hide the existence of the platform control plane from ordinary users.
        raise HTTPException(404, "platform_management_not_found")
    return role


def _memory_snapshot() -> dict[str, int | None]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes": values.get("MemAvailable"),
    }


def _host_snapshot() -> dict:
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    disk = shutil.disk_usage("/")
    return {
        "cpu_count": os.cpu_count() or 1,
        "load_average_1m": round(load[0], 2),
        "load_average_5m": round(load[1], 2),
        "load_average_15m": round(load[2], 2),
        "memory": _memory_snapshot(),
        "disk": {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
        },
        "scope": "current_api_host",
    }


@router.get("/context")
async def platform_management_context(
    ctx: AuthContext = Depends(current_user),
) -> dict:
    return {"role": await _platform_role(ctx)}


@router.get("/overview")
async def platform_management_overview(
    ctx: AuthContext = Depends(current_user),
) -> dict:
    role = await _platform_role(ctx)
    now = datetime.now(timezone.utc)
    online_since = now - timedelta(minutes=5)
    registered_since = now - timedelta(hours=24)
    async with short_admin_session() as session:
        total_users, active_users, registered_24h = (
            await session.execute(
                select(
                    func.count(User.user_id),
                    func.count(User.user_id).filter(User.status == "active"),
                    func.count(User.user_id).filter(User.created_at >= registered_since),
                )
            )
        ).one()
        online_users = int((await session.execute(
            select(func.count(distinct(Session.user_id))).where(
                Session.expires_at > now,
                Session.last_seen_at >= online_since,
                Session.audience == "web",
            )
        )).scalar_one())
        personal_count, business_count = (
            await session.execute(
                select(
                    func.count(Organization.tenant_id).filter(Organization.kind == "personal"),
                    func.count(Organization.tenant_id).filter(Organization.kind == "business"),
                )
            )
        ).one()
        business_rows = (
            await session.execute(
                select(
                    Organization.tenant_id,
                    Organization.name,
                    func.count(OrgMembership.membership_id).label("member_count"),
                    func.count(OrgMembership.membership_id).filter(
                        OrgMembership.status == "active"
                    ).label("active_member_count"),
                )
                .outerjoin(
                    OrgMembership,
                    OrgMembership.tenant_id == Organization.tenant_id,
                )
                .where(Organization.kind == "business")
                .group_by(Organization.tenant_id, Organization.name)
                .order_by(Organization.created_at.desc(), Organization.tenant_id)
                .limit(200)
            )
        ).all()

    return {
        "role": role,
        "generated_at": now,
        "identity": {
            "registered_users": int(total_users),
            "active_users": int(active_users),
            "online_users_5m": online_users,
            "registered_users_24h": int(registered_24h),
            "personal_workspaces": int(personal_count),
            "company_workspaces": int(business_count),
        },
        "organizations": [
            {
                "organization_id": str(row.tenant_id),
                "name": row.name,
                "member_count": int(row.member_count),
                "active_member_count": int(row.active_member_count),
            }
            for row in business_rows
        ],
        "host": _host_snapshot(),
        "sandboxes": await get_sandbox_manager().operational_snapshot(),
        "privacy": {
            "content_visible": False,
            "user_profiles_visible": False,
            "scope": "aggregate_and_lifecycle_metadata_only",
        },
    }


@router.get("/audit")
async def platform_management_audit(
    window_hours: int = Query(default=168, ge=1, le=24 * 31),
    ctx: AuthContext = Depends(current_user),
) -> dict:
    """Return a content-free, cross-tenant audit projection for operators.

    Private audit ciphertext is never decrypted here. Actor, tenant, target,
    network, user-agent and customer resource identifiers are intentionally
    absent from the response.
    """
    role = await _platform_role(ctx)
    now = datetime.now(timezone.utc)
    ts_from = now - timedelta(hours=window_hours)
    bucket_name = "hour" if window_hours <= 48 else "day"

    async with short_admin_session() as session:
        action_rows = (
            await session.execute(
                select(AuditLog.action, AuditLog.outcome, func.count(AuditLog.audit_id))
                .where(AuditLog.created_at >= ts_from)
                .group_by(AuditLog.action, AuditLog.outcome)
            )
        ).all()
        series_rows = (
            await session.execute(
                select(
                    func.date_trunc(bucket_name, AuditLog.created_at).label("bucket"),
                    AuditLog.action,
                    AuditLog.outcome,
                    func.count(AuditLog.audit_id),
                )
                .where(AuditLog.created_at >= ts_from)
                .group_by("bucket", AuditLog.action, AuditLog.outcome)
                .order_by("bucket")
            )
        ).all()
        recent_rows = (
            await session.execute(
                select(
                    AuditLog.audit_id,
                    AuditLog.action,
                    AuditLog.target_type,
                    AuditLog.outcome,
                    AuditLog.created_at,
                )
                .where(AuditLog.created_at >= ts_from)
                .order_by(AuditLog.created_at.desc(), AuditLog.audit_id.desc())
                .limit(100)
            )
        ).all()

    summaries = {
        category: {"category": category, "total": 0, "failures": 0}
        for category in AUDIT_CATEGORY_ORDER
    }
    action_breakdown: dict[str, dict[str, dict[str, int | str]]] = {
        category: {} for category in AUDIT_CATEGORY_ORDER
    }
    for action, outcome, count in action_rows:
        category = _audit_category(action)
        amount = int(count)
        summaries[category]["total"] += amount
        if outcome == "failure":
            summaries[category]["failures"] += amount
        item = action_breakdown[category].setdefault(
            action,
            {"action": action, "total": 0, "failures": 0},
        )
        item["total"] = int(item["total"]) + amount
        if outcome == "failure":
            item["failures"] = int(item["failures"]) + amount

    series_by_category: dict[str, dict[datetime, dict]] = {
        category: {} for category in AUDIT_CATEGORY_ORDER
    }
    for bucket, action, outcome, count in series_rows:
        category = _audit_category(action)
        point = series_by_category[category].setdefault(
            bucket,
            {"ts": bucket, "total": 0, "failures": 0},
        )
        amount = int(count)
        point["total"] += amount
        if outcome == "failure":
            point["failures"] += amount

    categories = []
    for category in AUDIT_CATEGORY_ORDER:
        categories.append({
            **summaries[category],
            "series": list(series_by_category[category].values()),
            "actions": sorted(
                action_breakdown[category].values(),
                key=lambda item: (-int(item["total"]), str(item["action"])),
            ),
        })

    return {
        "role": role,
        "generated_at": now,
        "window_hours": window_hours,
        "bucket": bucket_name,
        "categories": categories,
        "recent_events": [
            {
                "event_id": str(row.audit_id),
                "category": _audit_category(row.action),
                "action": row.action,
                "target_type": row.target_type,
                "outcome": row.outcome,
                "created_at": row.created_at,
            }
            for row in recent_rows
        ],
        "catalog": _audit_catalog(),
        "privacy": {
            "content_visible": False,
            "identities_visible": False,
            "customer_resource_identifiers_visible": False,
            "private_payload_decrypted": False,
        },
    }

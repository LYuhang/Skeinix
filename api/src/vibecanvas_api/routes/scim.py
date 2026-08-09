"""SCIM 2.0 user and IdP-managed group provisioning endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any
import uuid

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from vibecanvas_api.audit import actions as audit_actions
from vibecanvas_api.audit.context import extract_request_audit_context
from vibecanvas_api.audit.service import record_audit
from vibecanvas_api.authorization.dependencies import (
    mutation_coordinator_for_request,
)
from vibecanvas_api.authorization.projection import (
    apply_committed_structural_mutations,
    enqueue_structural_delta,
    group_edges,
    group_membership_edges,
    organization_membership_edges,
)
from vibecanvas_api.security.enterprise_identity import (
    decrypt_directory_user_private,
    directory_lookup_digest,
    encrypt_directory_user_private,
)
from vibecanvas_api.security.identity_protection import (
    decrypt_user_profile,
    encrypt_user_profile,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models import Session, User
from vibecanvas_api.storage.models_enterprise_identity import (
    EnterpriseDirectoryUser,
    EnterpriseIdentityProvider,
)
from vibecanvas_api.storage.models_org import (
    Group,
    GroupMembership,
    OrgMembership,
)
from vibecanvas_api.storage.repo_enterprise_identity import (
    EnterpriseIdentityRepo,
)

router = APIRouter(prefix="/scim/v2/{provider_id}", tags=["scim"])

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SP_CONFIG_SCHEMA = (
    "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
)
RESOURCE_TYPE_SCHEMA = (
    "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
)
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
_MEDIA_TYPE = "application/scim+json"
_FILTER = re.compile(
    r'^\s*([A-Za-z][A-Za-z0-9._:-]*)\s+eq\s+("(?:[^"\\]|\\.)*")\s*$',
    re.IGNORECASE,
)
_MEMBER_FILTER_PATH = re.compile(
    r'^members\s*\[\s*value\s+eq\s+("(?:[^"\\]|\\.)*")\s*\]$',
    re.IGNORECASE,
)


class ScimProtocolError(Exception):
    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        scim_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.scim_type = scim_type
        self.headers = headers or {}


async def scim_exception_handler(
    _request: Request,
    exc: ScimProtocolError,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "schemas": [ERROR_SCHEMA],
        "status": str(exc.status_code),
        "detail": exc.detail,
    }
    if exc.scim_type:
        payload["scimType"] = exc.scim_type
    return JSONResponse(
        payload,
        status_code=exc.status_code,
        headers=exc.headers,
        media_type=_MEDIA_TYPE,
    )


@dataclass(frozen=True, slots=True)
class ScimContext:
    provider_id: uuid.UUID
    tenant_id: uuid.UUID


def _json(payload: Any, *, status_code: int = 200, headers=None) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers=headers,
        media_type=_MEDIA_TYPE,
    )


async def _request_json(request: Request) -> Any:
    """Decode SCIM JSON without falling through to FastAPI's generic errors."""
    try:
        return await request.json()
    except (UnicodeDecodeError, ValueError) as exc:
        raise ScimProtocolError(
            400,
            "The request body is not valid JSON.",
            scim_type="invalidSyntax",
        ) from exc


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _organization_timestamp() -> datetime:
    """UTC value for legacy organization columns stored without timezone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def require_scim_context(
    provider_id: uuid.UUID,
    authorization: str | None = Header(default=None),
) -> ScimContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise ScimProtocolError(
            401,
            "A valid SCIM bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw = authorization[7:].strip()
    if not raw or len(raw) > 512:
        raise ScimProtocolError(401, "The SCIM bearer token is invalid.")
    async with session_scope() as session:
        provider = await EnterpriseIdentityRepo(session).get_provider(
            provider_id,
            active_only=True,
        )
    if (
        provider is None
        or (
            provider.scim_token_expires_at is not None
            and provider.scim_token_expires_at <= datetime.now(timezone.utc)
        )
        or not hmac.compare_digest(
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            provider.scim_token_hash,
        )
    ):
        raise ScimProtocolError(
            401,
            "The SCIM bearer token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ScimContext(provider.provider_id, provider.tenant_id)


def _parse_filter(
    value: str | None,
    *,
    allowed: set[str],
) -> tuple[str, str] | None:
    if value is None:
        return None
    match = _FILTER.fullmatch(value)
    if match is None:
        raise ScimProtocolError(
            400,
            "Only a single equality filter is supported.",
            scim_type="invalidFilter",
        )
    attribute = match.group(1).casefold()
    if attribute not in {item.casefold() for item in allowed}:
        raise ScimProtocolError(
            400,
            "The filter attribute is not supported.",
            scim_type="invalidFilter",
        )
    try:
        operand = json.loads(match.group(2))
    except ValueError as exc:
        raise ScimProtocolError(
            400,
            "The filter value is invalid.",
            scim_type="invalidFilter",
        ) from exc
    return attribute, operand


def _page(resources: list[dict], start_index: int, count: int) -> dict:
    start = max(0, start_index - 1)
    items = resources[start:start + count]
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(resources),
        "startIndex": start_index,
        "itemsPerPage": len(items),
        "Resources": items,
    }


def _parse_user_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScimProtocolError(400, "The User payload must be an object.")
    user_name = payload.get("userName")
    if not isinstance(user_name, str) or not user_name.strip() or len(user_name) > 320:
        raise ScimProtocolError(400, "userName is required.", scim_type="invalidValue")
    user_name = user_name.strip()
    external_id = payload.get("externalId")
    if external_id is None:
        external_id = user_name
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 1024:
        raise ScimProtocolError(400, "externalId is invalid.", scim_type="invalidValue")
    emails = payload.get("emails")
    email = None
    if isinstance(emails, list):
        values = [
            item for item in emails
            if isinstance(item, dict) and isinstance(item.get("value"), str)
        ]
        primary = next((item for item in values if item.get("primary") is True), None)
        if primary is not None:
            email = primary["value"]
        elif values:
            email = values[0]["value"]
    if email is None and "@" in user_name:
        email = user_name
    if not isinstance(email, str):
        raise ScimProtocolError(400, "A valid email is required.", scim_type="invalidValue")
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ScimProtocolError(
            400,
            "A valid email is required.",
            scim_type="invalidValue",
        ) from exc
    display_name = payload.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        name = payload.get("name")
        display_name = name.get("formatted") if isinstance(name, dict) else None
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = email
    display_name = " ".join(display_name.split())
    if len(display_name) > 256:
        raise ScimProtocolError(400, "displayName is too long.", scim_type="invalidValue")
    active = payload.get("active", True)
    if not isinstance(active, bool):
        raise ScimProtocolError(400, "active must be boolean.", scim_type="invalidValue")
    return {
        "external_id": external_id.strip(),
        "user_name": user_name,
        "email": email,
        "display_name": display_name,
        "active": active,
    }


async def _user_resource(session, row: EnterpriseDirectoryUser) -> dict:
    user = await session.get(User, row.user_id)
    if user is None:
        raise ScimProtocolError(404, "User not found.")
    private = await decrypt_directory_user_private(session, row)
    profile = await decrypt_user_profile(session, user)
    return {
        "schemas": [USER_SCHEMA],
        "id": str(row.directory_user_id),
        "externalId": private.external_id,
        "userName": private.user_name,
        "displayName": profile.display_name,
        "name": {"formatted": profile.display_name},
        "emails": [{"value": profile.email, "primary": True}],
        "active": bool(row.active),
        "meta": {
            "resourceType": "User",
            "created": _iso(row.created_at),
            "lastModified": _iso(row.updated_at),
            "location": f"/scim/v2/{row.provider_id}/Users/{row.directory_user_id}",
        },
    }


async def _record_sync(
    session,
    *,
    request: Request,
    provider: EnterpriseIdentityProvider,
    operation: str,
    target_id: str,
) -> None:
    provider.last_scim_sync_at = datetime.now(timezone.utc)
    provider.updated_at = datetime.now(timezone.utc)
    await record_audit(
        session,
        action=audit_actions.ENTERPRISE_IDENTITY_SCIM_SYNC,
        actor_user_id=None,
        actor_email=None,
        target_type=audit_actions.TARGET_ENTERPRISE_IDENTITY,
        target_id=target_id,
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={"provider_id": str(provider.provider_id), "operation": operation},
    )


async def _write_user(
    *,
    request: Request,
    context: ScimContext,
    values: dict[str, Any],
    existing: EnterpriseDirectoryUser | None,
) -> EnterpriseDirectoryUser:
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        repo = EnterpriseIdentityRepo(session)
        provider = await repo.get_provider(
            context.provider_id,
            tenant_id=context.tenant_id,
            active_only=True,
        )
        if provider is None:
            raise ScimProtocolError(401, "The SCIM provider is disabled.")
        if existing is not None:
            row = await repo.directory_user_by_id(
                context.provider_id,
                existing.directory_user_id,
            )
            if row is None:
                raise ScimProtocolError(404, "User not found.")
            user = await session.get(User, row.user_id)
            membership = (await session.execute(
                select(OrgMembership).where(
                    OrgMembership.user_id == row.user_id,
                    OrgMembership.tenant_id == context.tenant_id,
                )
            )).scalar_one_or_none()
            if user is None or membership is None:
                raise ScimProtocolError(409, "The directory user is inconsistent.")
            before = organization_membership_edges(
                organization_id=str(context.tenant_id),
                user_id=str(user.user_id),
                role=membership.org_role,
                status=membership.status,
            )
        else:
            if await repo.directory_user_by_external_id(
                context.provider_id,
                values["external_id"],
            ) is not None:
                raise ScimProtocolError(
                    409,
                    "externalId is already in use.",
                    scim_type="uniqueness",
                )
            if await repo.directory_user_by_user_name(
                context.provider_id,
                values["user_name"],
            ) is not None:
                raise ScimProtocolError(
                    409,
                    "userName is already in use.",
                    scim_type="uniqueness",
                )
            user_id = uuid.uuid4()
            user = User(
                user_id=user_id,
                tenant_id=context.tenant_id,
                email_sentinel=f"redacted-{user_id}@invalid.local",
                display_name_sentinel="",
                status="active",
            )
            session.add(user)
            await session.flush()
            membership = OrgMembership(
                user_id=user_id,
                tenant_id=context.tenant_id,
                org_role="member",
                status="active" if values["active"] else "suspended",
                source="scim",
                directory_provider_id=context.provider_id,
            )
            session.add(membership)
            directory_user_id = uuid.uuid4()
            before = frozenset()

        profile = await encrypt_user_profile(
            session,
            user_id=user.user_id,
            tenant_id=context.tenant_id,
            email=values["email"],
            display_name=values["display_name"],
        )
        user.profile_ciphertext = profile.ciphertext
        user.profile_nonce = profile.nonce
        user.profile_key_id = profile.key_id
        private = await encrypt_directory_user_private(
            session,
            directory_user_id=(
                row.directory_user_id if existing is not None
                else directory_user_id
            ),
            provider_id=context.provider_id,
            tenant_id=context.tenant_id,
            user_id=user.user_id,
            external_id=values["external_id"],
            user_name=values["user_name"],
        )
        external_id_lookup_hash = directory_lookup_digest(
            context.provider_id,
            "external_id",
            values["external_id"],
        )
        user_name_lookup_hash = directory_lookup_digest(
            context.provider_id,
            "user_name",
            values["user_name"],
            casefold=True,
        )
        if existing is None:
            row = EnterpriseDirectoryUser(
                directory_user_id=directory_user_id,
                provider_id=context.provider_id,
                tenant_id=context.tenant_id,
                user_id=user_id,
                external_id_lookup_hash=external_id_lookup_hash,
                user_name_lookup_hash=user_name_lookup_hash,
                private_ciphertext=private.ciphertext,
                private_nonce=private.nonce,
                private_key_id=private.key_id,
                active=values["active"],
            )
            session.add(row)
        else:
            row.external_id_lookup_hash = external_id_lookup_hash
            row.user_name_lookup_hash = user_name_lookup_hash
            row.private_ciphertext = private.ciphertext
            row.private_nonce = private.nonce
            row.private_key_id = private.key_id
            row.active = values["active"]
            row.updated_at = datetime.now(timezone.utc)
        membership.source = "scim"
        membership.directory_provider_id = context.provider_id
        membership.status = "active" if values["active"] else "suspended"
        membership.updated_at = _organization_timestamp()
        if not values["active"]:
            await session.execute(
                delete(Session).where(
                    Session.user_id == user.user_id,
                    Session.active_organization_id == context.tenant_id,
                )
            )
        after = organization_membership_edges(
            organization_id=str(context.tenant_id),
            user_id=str(user.user_id),
            role=membership.org_role,
            status=membership.status,
        )
        coordinator = mutation_coordinator_for_request(
            request,
            str(context.tenant_id),
        )
        mutation_ids = await enqueue_structural_delta(
            session=session,
            coordinator=coordinator,
            actor_type="system",
            actor_id=str(context.provider_id),
            before=before,
            after=after,
            operation_id=uuid.uuid4().hex,
            source="scim-user-sync",
        )
        await _record_sync(
            session,
            request=request,
            provider=provider,
            operation="user_upsert" if existing is not None else "user_create",
            target_id=str(row.directory_user_id),
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ScimProtocolError(
                409,
                "A directory identity attribute is already in use.",
                scim_type="uniqueness",
            ) from exc
        await session.commit()
        await apply_committed_structural_mutations(coordinator, mutation_ids)
        # Rebind for the response projection after the explicit commit.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(context.tenant_id)},
        )
        await session.refresh(row)
        return row


async def _get_directory_user(
    context: ScimContext,
    directory_user_id: uuid.UUID,
) -> EnterpriseDirectoryUser:
    async with session_scope() as session:
        row = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            directory_user_id,
        )
    if row is None:
        raise ScimProtocolError(404, "User not found.")
    return row


@router.get("/ServiceProviderConfig")
async def service_provider_config(
    _context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    return _json({
        "schemas": [SP_CONFIG_SCHEMA],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 100},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "Bearer Token",
            "description": "Rotatable organization SCIM bearer token",
            "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
            "primary": True,
        }],
    })


@router.get("/ResourceTypes")
async def resource_types(
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    base = f"/scim/v2/{context.provider_id}"
    return _json(_page([
        {
            "schemas": [RESOURCE_TYPE_SCHEMA],
            "id": "User",
            "name": "User",
            "endpoint": f"{base}/Users",
            "schema": USER_SCHEMA,
        },
        {
            "schemas": [RESOURCE_TYPE_SCHEMA],
            "id": "Group",
            "name": "Group",
            "endpoint": f"{base}/Groups",
            "schema": GROUP_SCHEMA,
        },
    ], 1, 100))


@router.get("/Schemas")
async def schemas(
    _context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    return _json(_page([
        {"schemas": [SCHEMA_SCHEMA], "id": USER_SCHEMA, "name": "User"},
        {"schemas": [SCHEMA_SCHEMA], "id": GROUP_SCHEMA, "name": "Group"},
    ], 1, 100))


@router.post("/Users")
async def create_user(
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    values = _parse_user_payload(await _request_json(request))
    row = await _write_user(
        request=request,
        context=context,
        values=values,
        existing=None,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            row.directory_user_id,
        )
        resource = await _user_resource(session, current)
    location = resource["meta"]["location"]
    return _json(resource, status_code=201, headers={"Location": location})


@router.get("/Users/{directory_user_id}")
async def get_user(
    directory_user_id: uuid.UUID,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        row = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            directory_user_id,
        )
        if row is None:
            raise ScimProtocolError(404, "User not found.")
        return _json(await _user_resource(session, row))


@router.get("/Users")
async def list_users(
    filter: str | None = Query(default=None, max_length=2048),
    start_index: int = Query(default=1, alias="startIndex", ge=1),
    count: int = Query(default=100, ge=0, le=100),
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    parsed = _parse_filter(filter, allowed={"id", "externalId", "userName"})
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        repo = EnterpriseIdentityRepo(session)
        rows: list[EnterpriseDirectoryUser]
        if parsed is None:
            rows = await repo.list_directory_users(context.provider_id)
        elif parsed[0] == "id":
            try:
                row = await repo.directory_user_by_id(
                    context.provider_id,
                    uuid.UUID(parsed[1]),
                )
            except ValueError:
                row = None
            rows = [row] if row is not None else []
        elif parsed[0] == "externalid":
            row = await repo.directory_user_by_external_id(
                context.provider_id,
                parsed[1],
            )
            rows = [row] if row is not None else []
        else:
            row = await repo.directory_user_by_user_name(
                context.provider_id,
                parsed[1],
            )
            rows = [row] if row is not None else []
        resources = [await _user_resource(session, row) for row in rows]
    return _json(_page(resources, start_index, count))


@router.put("/Users/{directory_user_id}")
async def replace_user(
    directory_user_id: uuid.UUID,
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    existing = await _get_directory_user(context, directory_user_id)
    row = await _write_user(
        request=request,
        context=context,
        values=_parse_user_payload(await _request_json(request)),
        existing=existing,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            row.directory_user_id,
        )
        return _json(await _user_resource(session, current))


def _apply_user_patch(current: dict, payload: Any) -> dict:
    if not isinstance(payload, dict) or PATCH_SCHEMA not in payload.get("schemas", []):
        raise ScimProtocolError(400, "A SCIM PatchOp payload is required.")
    operations = payload.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise ScimProtocolError(400, "Patch operations are required.")
    next_value = dict(current)
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimProtocolError(400, "A patch operation is invalid.")
        op = str(operation.get("op") or "").casefold()
        path = operation.get("path")
        value = operation.get("value")
        if op not in {"add", "replace", "remove"}:
            raise ScimProtocolError(400, "The patch operation is unsupported.")
        if path is None and op in {"add", "replace"} and isinstance(value, dict):
            next_value.update(value)
            continue
        normalized_path = str(path or "").casefold()
        if normalized_path not in {
            "username", "externalid", "displayname", "active", "emails", "name",
        }:
            raise ScimProtocolError(400, "The patch path is unsupported.")
        canonical = {
            "username": "userName",
            "externalid": "externalId",
            "displayname": "displayName",
        }.get(normalized_path, normalized_path)
        if op == "remove":
            next_value.pop(canonical, None)
        else:
            next_value[canonical] = value
    return next_value


@router.patch("/Users/{directory_user_id}")
async def patch_user(
    directory_user_id: uuid.UUID,
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    existing = await _get_directory_user(context, directory_user_id)
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current_row = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            directory_user_id,
        )
        current = await _user_resource(session, current_row)
    patched = _apply_user_patch(current, await _request_json(request))
    row = await _write_user(
        request=request,
        context=context,
        values=_parse_user_payload(patched),
        existing=existing,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current_row = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            row.directory_user_id,
        )
        return _json(await _user_resource(session, current_row))


@router.delete("/Users/{directory_user_id}", status_code=204)
async def delete_user(
    directory_user_id: uuid.UUID,
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> Response:
    existing = await _get_directory_user(context, directory_user_id)
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        row = await EnterpriseIdentityRepo(session).directory_user_by_id(
            context.provider_id,
            directory_user_id,
        )
        current = await _user_resource(session, row)
    current["active"] = False
    await _write_user(
        request=request,
        context=context,
        values=_parse_user_payload(current),
        existing=existing,
    )
    return Response(status_code=204)


def _parse_group_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScimProtocolError(400, "The Group payload must be an object.")
    display_name = payload.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ScimProtocolError(400, "displayName is required.", scim_type="invalidValue")
    display_name = " ".join(display_name.split())
    if len(display_name) > 120:
        raise ScimProtocolError(400, "displayName is too long.", scim_type="invalidValue")
    external_id = payload.get("externalId")
    if external_id is not None and (
        not isinstance(external_id, str)
        or not external_id.strip()
        or len(external_id) > 1024
    ):
        raise ScimProtocolError(400, "externalId is invalid.", scim_type="invalidValue")
    members = payload.get("members", [])
    if not isinstance(members, list):
        raise ScimProtocolError(400, "members must be an array.", scim_type="invalidValue")
    member_ids: list[uuid.UUID] = []
    for member in members:
        if not isinstance(member, dict) or not isinstance(member.get("value"), str):
            raise ScimProtocolError(400, "A group member is invalid.", scim_type="invalidValue")
        if member.get("type") not in {None, "User"}:
            raise ScimProtocolError(
                400,
                "Only direct User members are supported.",
                scim_type="invalidValue",
            )
        try:
            member_ids.append(uuid.UUID(member["value"]))
        except ValueError as exc:
            raise ScimProtocolError(400, "A group member id is invalid.") from exc
    return {
        "display_name": display_name,
        "external_id": external_id.strip() if isinstance(external_id, str) else None,
        "member_ids": list(dict.fromkeys(member_ids)),
    }


async def _group_resource(session, group: Group) -> dict:
    membership_rows = list((await session.execute(
        select(GroupMembership, EnterpriseDirectoryUser)
        .join(
            EnterpriseDirectoryUser,
            EnterpriseDirectoryUser.user_id == GroupMembership.user_id,
        )
        .where(
            GroupMembership.group_id == group.group_id,
            GroupMembership.status == "active",
            GroupMembership.source == "idp",
            EnterpriseDirectoryUser.provider_id == group.directory_provider_id,
        )
        .order_by(EnterpriseDirectoryUser.directory_user_id)
    )).all())
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(group.group_id),
        **({"externalId": group.external_id} if group.external_id else {}),
        "displayName": group.name,
        "members": [
            {
                "value": str(directory_user.directory_user_id),
                "type": "User",
                "$ref": (
                    f"/scim/v2/{group.directory_provider_id}/Users/"
                    f"{directory_user.directory_user_id}"
                ),
            }
            for _, directory_user in membership_rows
        ],
        "meta": {
            "resourceType": "Group",
            "created": _iso(group.created_at),
            "lastModified": _iso(group.updated_at),
            "location": f"/scim/v2/{group.directory_provider_id}/Groups/{group.group_id}",
        },
    }


async def _write_group(
    *,
    request: Request,
    context: ScimContext,
    values: dict[str, Any],
    group_id: uuid.UUID | None,
) -> Group:
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        provider = await EnterpriseIdentityRepo(session).get_provider(
            context.provider_id,
            tenant_id=context.tenant_id,
            active_only=True,
        )
        if provider is None:
            raise ScimProtocolError(401, "The SCIM provider is disabled.")
        group = await session.get(Group, group_id) if group_id else None
        if group_id is not None and (
            group is None or group.directory_provider_id != context.provider_id
        ):
            raise ScimProtocolError(404, "Group not found.")
        if group is None:
            group = Group(
                group_id=uuid.uuid4(),
                tenant_id=context.tenant_id,
                parent_group_id=None,
                kind="team",
                name=values["display_name"],
                source="idp",
                directory_provider_id=context.provider_id,
                external_id=values["external_id"],
                external_id_lookup_hash="pending",
                status="active",
                created_by=provider.created_by,
            )
            session.add(group)
            before = frozenset()
            operation = "group_create"
        else:
            before_set = set(group_edges(
                organization_id=str(context.tenant_id),
                group_id=str(group.group_id),
                parent_group_id=None,
                status=group.status,
            ))
            existing_memberships = list((await session.execute(
                select(GroupMembership).where(
                    GroupMembership.group_id == group.group_id,
                    GroupMembership.status == "active",
                )
            )).scalars())
            for membership in existing_memberships:
                before_set.update(group_membership_edges(
                    organization_id=str(context.tenant_id),
                    group_id=str(group.group_id),
                    user_id=str(membership.user_id),
                    role=membership.group_role,
                    status=membership.status,
                ))
            before = frozenset(before_set)
            operation = "group_update"
        group.name = values["display_name"]
        group.external_id = values["external_id"]
        external_key = values["external_id"] or f"name:{values['display_name'].casefold()}"
        group.external_id_lookup_hash = directory_lookup_digest(
            context.provider_id,
            "group_external_id",
            external_key,
        )
        group.status = "active"
        group.updated_at = _organization_timestamp()
        await session.flush()

        desired_users: dict[uuid.UUID, uuid.UUID] = {}
        for directory_user_id in values["member_ids"]:
            directory_user = await EnterpriseIdentityRepo(
                session
            ).directory_user_by_id(context.provider_id, directory_user_id)
            if directory_user is None or not directory_user.active:
                raise ScimProtocolError(
                    400,
                    "A group member is not an active User in this provider.",
                    scim_type="invalidValue",
                )
            desired_users[directory_user.user_id] = directory_user.directory_user_id
        current_memberships = {
            row.user_id: row
            for row in (await session.execute(
                select(GroupMembership).where(
                    GroupMembership.group_id == group.group_id,
                )
            )).scalars()
        }
        for user_id, membership in current_memberships.items():
            membership.status = "active" if user_id in desired_users else "revoked"
            membership.source = "idp"
            membership.updated_at = _organization_timestamp()
        for user_id in desired_users.keys() - current_memberships.keys():
            session.add(GroupMembership(
                tenant_id=context.tenant_id,
                group_id=group.group_id,
                user_id=user_id,
                group_role="member",
                status="active",
                source="idp",
            ))
        after_set = set(group_edges(
            organization_id=str(context.tenant_id),
            group_id=str(group.group_id),
            parent_group_id=None,
            status="active",
        ))
        for user_id in desired_users:
            after_set.update(group_membership_edges(
                organization_id=str(context.tenant_id),
                group_id=str(group.group_id),
                user_id=str(user_id),
                role="member",
                status="active",
            ))
        coordinator = mutation_coordinator_for_request(
            request,
            str(context.tenant_id),
        )
        mutation_ids = await enqueue_structural_delta(
            session=session,
            coordinator=coordinator,
            actor_type="system",
            actor_id=str(context.provider_id),
            before=before,
            after=frozenset(after_set),
            operation_id=uuid.uuid4().hex,
            source="scim-group-sync",
        )
        await _record_sync(
            session,
            request=request,
            provider=provider,
            operation=operation,
            target_id=str(group.group_id),
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ScimProtocolError(
                409,
                "A Group identity or displayName is already in use.",
                scim_type="uniqueness",
            ) from exc
        await session.commit()
        await apply_committed_structural_mutations(coordinator, mutation_ids)
        return group


@router.post("/Groups")
async def create_group(
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    group = await _write_group(
        request=request,
        context=context,
        values=_parse_group_payload(await _request_json(request)),
        group_id=None,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current = await session.get(Group, group.group_id)
        resource = await _group_resource(session, current)
    return _json(
        resource,
        status_code=201,
        headers={"Location": resource["meta"]["location"]},
    )


@router.get("/Groups/{group_id}")
async def get_group(
    group_id: uuid.UUID,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        group = await session.get(Group, group_id)
        if group is None or group.directory_provider_id != context.provider_id:
            raise ScimProtocolError(404, "Group not found.")
        return _json(await _group_resource(session, group))


@router.get("/Groups")
async def list_groups(
    filter: str | None = Query(default=None, max_length=2048),
    start_index: int = Query(default=1, alias="startIndex", ge=1),
    count: int = Query(default=100, ge=0, le=100),
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    parsed = _parse_filter(filter, allowed={"id", "externalId", "displayName"})
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        groups = list((await session.execute(
            select(Group)
            .where(
                Group.directory_provider_id == context.provider_id,
                Group.status == "active",
            )
            .order_by(Group.created_at, Group.group_id)
        )).scalars())
        if parsed is not None:
            attribute, operand = parsed
            if attribute == "id":
                groups = [row for row in groups if str(row.group_id) == operand]
            elif attribute == "externalid":
                groups = [row for row in groups if row.external_id == operand]
            else:
                groups = [row for row in groups if row.name.casefold() == operand.casefold()]
        resources = [await _group_resource(session, row) for row in groups]
    return _json(_page(resources, start_index, count))


@router.put("/Groups/{group_id}")
async def replace_group(
    group_id: uuid.UUID,
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    group = await _write_group(
        request=request,
        context=context,
        values=_parse_group_payload(await _request_json(request)),
        group_id=group_id,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current = await session.get(Group, group.group_id)
        return _json(await _group_resource(session, current))


def _apply_group_patch(current: dict, payload: Any) -> dict:
    if not isinstance(payload, dict) or PATCH_SCHEMA not in payload.get("schemas", []):
        raise ScimProtocolError(400, "A SCIM PatchOp payload is required.")
    operations = payload.get("Operations")
    if not isinstance(operations, list) or not operations:
        raise ScimProtocolError(400, "Patch operations are required.")
    next_value = dict(current)
    next_value["members"] = list(current.get("members") or [])
    for operation in operations:
        if not isinstance(operation, dict):
            raise ScimProtocolError(400, "A patch operation is invalid.")
        op = str(operation.get("op") or "").casefold()
        path = operation.get("path")
        value = operation.get("value")
        if op not in {"add", "replace", "remove"}:
            raise ScimProtocolError(400, "The patch operation is unsupported.")
        if path is None and op in {"add", "replace"} and isinstance(value, dict):
            next_value.update(value)
            continue
        normalized = str(path or "").strip()
        member_match = _MEMBER_FILTER_PATH.fullmatch(normalized)
        if member_match:
            if op != "remove":
                raise ScimProtocolError(400, "Filtered members only support remove.")
            member_id = json.loads(member_match.group(1))
            next_value["members"] = [
                item for item in next_value["members"]
                if not isinstance(item, dict) or item.get("value") != member_id
            ]
            continue
        path_key = normalized.casefold()
        if path_key == "members":
            member_values = value.get("members") if isinstance(value, dict) else value
            if op == "remove":
                next_value["members"] = []
            elif not isinstance(member_values, list):
                raise ScimProtocolError(400, "members patch value must be an array.")
            elif op == "replace":
                next_value["members"] = member_values
            else:
                next_value["members"].extend(member_values)
        elif path_key in {"displayname", "externalid"}:
            canonical = "displayName" if path_key == "displayname" else "externalId"
            if op == "remove":
                next_value.pop(canonical, None)
            else:
                next_value[canonical] = value
        else:
            raise ScimProtocolError(400, "The patch path is unsupported.")
    return next_value


@router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: uuid.UUID,
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> JSONResponse:
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        group = await session.get(Group, group_id)
        if group is None or group.directory_provider_id != context.provider_id:
            raise ScimProtocolError(404, "Group not found.")
        current = await _group_resource(session, group)
    patched = _apply_group_patch(current, await _request_json(request))
    updated = await _write_group(
        request=request,
        context=context,
        values=_parse_group_payload(patched),
        group_id=group_id,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        current_group = await session.get(Group, updated.group_id)
        return _json(await _group_resource(session, current_group))


@router.delete("/Groups/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    context: ScimContext = Depends(require_scim_context),
) -> Response:
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        provider = await EnterpriseIdentityRepo(session).get_provider(
            context.provider_id,
            tenant_id=context.tenant_id,
            active_only=True,
        )
        group = await session.get(Group, group_id)
        if (
            provider is None
            or group is None
            or group.directory_provider_id != context.provider_id
        ):
            raise ScimProtocolError(404, "Group not found.")
        before = set(group_edges(
            organization_id=str(context.tenant_id),
            group_id=str(group.group_id),
            parent_group_id=None,
            status=group.status,
        ))
        memberships = list((await session.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.group_id,
                GroupMembership.status == "active",
            )
        )).scalars())
        for membership in memberships:
            before.update(group_membership_edges(
                organization_id=str(context.tenant_id),
                group_id=str(group.group_id),
                user_id=str(membership.user_id),
                role=membership.group_role,
                status=membership.status,
            ))
            membership.status = "revoked"
        group.status = "archived"
        group.updated_at = _organization_timestamp()
        coordinator = mutation_coordinator_for_request(
            request,
            str(context.tenant_id),
        )
        mutation_ids = await enqueue_structural_delta(
            session=session,
            coordinator=coordinator,
            actor_type="system",
            actor_id=str(context.provider_id),
            before=frozenset(before),
            after=frozenset(),
            operation_id=uuid.uuid4().hex,
            source="scim-group-delete",
        )
        await _record_sync(
            session,
            request=request,
            provider=provider,
            operation="group_delete",
            target_id=str(group.group_id),
        )
        await session.commit()
        await apply_committed_structural_mutations(coordinator, mutation_ids)
    return Response(status_code=204)


__all__ = ["ScimProtocolError", "router", "scim_exception_handler"]

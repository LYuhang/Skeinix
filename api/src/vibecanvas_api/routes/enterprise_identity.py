"""Enterprise identity-provider administration and OIDC login routes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.audit import actions as audit_actions
from vibecanvas_api.audit.context import extract_request_audit_context
from vibecanvas_api.audit.service import record_audit, record_auth_audit
from vibecanvas_api.auth.deps import (
    AuthContext,
    current_user,
    require_recent_step_up,
    tenant_db,
)
from vibecanvas_api.auth.oidc import (
    OidcError,
    create_login_transaction,
    discover_oidc,
    exchange_code,
    mapped_identity_claims,
    resolve_transaction_bundle,
    validate_id_token,
    validate_return_to,
)
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.auth.ratelimit import (
    LoginRateLimitExceeded,
    LoginRateLimitUnavailable,
    consume_rate_limited_action,
)
from vibecanvas_api.auth.session_security import set_session_cookies
from vibecanvas_api.auth.tokens import new_token
from vibecanvas_api.authorization.dependencies import (
    authorize_resource,
    get_authz_service,
)
from vibecanvas_api.authorization.service import AuthzService
from vibecanvas_api.authorization.types import Action, ResourceRef, ResourceType
from vibecanvas_api.config import config
from vibecanvas_api.security.secret_service import secret_service
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models import Session
from vibecanvas_api.storage.models_enterprise_identity import (
    EnterpriseDirectoryUser,
    EnterpriseIdentityProvider,
)
from vibecanvas_api.storage.models_org import Organization, OrgMembership
from vibecanvas_api.storage.models_secrets import EncryptedSecret
from vibecanvas_api.storage.repo_enterprise_identity import (
    EnterpriseIdentityRepo,
)


router = APIRouter(tags=["enterprise-identity"])
_SESSION_TTL = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_sso_login_enabled() -> None:
    """Fail closed before discovery, redirects, or transaction processing."""
    if not config.enterprise_sso_enabled:
        # Treat a disabled optional login surface as unavailable instead of
        # disclosing configured organizations/providers to direct callers.
        raise HTTPException(404, "enterprise_sso_disabled")


def _new_scim_token() -> tuple[str, str]:
    raw = "vc_scim_" + secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _absolute_external_url(path: str) -> str | None:
    try:
        return config.public_urls.absolute(path)
    except ValueError:
        return None


async def _limit_sso_request(request: Request, surface: str) -> None:
    client_host = request.client.host if request.client else "unknown"
    try:
        await consume_rate_limited_action(
            f"sso:{surface}:{client_host}",
            max_attempts=30,
            window_seconds=300,
        )
    except LoginRateLimitExceeded:
        raise HTTPException(
            429,
            "sso_rate_limited",
            headers={"Retry-After": "300"},
        ) from None
    except LoginRateLimitUnavailable:
        raise HTTPException(503, "authentication_security_service_unavailable") from None


def _validated_scopes(value: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    if "openid" not in normalized:
        raise ValueError("OIDC scopes must include openid")
    if any(len(item) > 128 or " " in item for item in normalized):
        raise ValueError("OIDC scope is invalid")
    return normalized


class IdentityProviderCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    issuer_url: str = Field(min_length=8, max_length=2048)
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: SecretStr | None = None
    token_endpoint_auth_method: str | None = Field(
        default=None,
        pattern="^(client_secret_basic|client_secret_post|none)$",
    )
    subject_claim: str = Field(default="sub", min_length=1, max_length=128)
    email_claim: str = Field(default="email", min_length=1, max_length=128)
    display_name_claim: str = Field(default="name", min_length=1, max_length=128)
    scopes: list[str] = Field(
        default_factory=lambda: ["openid", "profile", "email"],
        min_length=1,
        max_length=16,
    )
    scim_token_ttl_days: int = Field(default=365, ge=1, le=365)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        return _validated_scopes(value)


class IdentityProviderPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    client_secret: SecretStr | None = None
    token_endpoint_auth_method: str | None = Field(
        default=None,
        pattern="^(client_secret_basic|client_secret_post|none)$",
    )
    subject_claim: str | None = Field(default=None, min_length=1, max_length=128)
    email_claim: str | None = Field(default=None, min_length=1, max_length=128)
    display_name_claim: str | None = Field(default=None, min_length=1, max_length=128)
    scopes: list[str] | None = Field(default=None, min_length=1, max_length=16)


class ScimTokenRotate(BaseModel):
    ttl_days: int = Field(default=365, ge=1, le=365)


class IdentityProviderOut(BaseModel):
    provider_id: uuid.UUID
    organization_id: uuid.UUID
    display_name: str
    issuer_url: str
    client_id: str
    token_endpoint_auth_method: Literal[
        "client_secret_basic", "client_secret_post", "none"
    ]
    has_client_secret: bool
    subject_claim: str
    email_claim: str
    display_name_claim: str
    scopes: list[str]
    status: Literal["active", "disabled"]
    scim_token_generation: int
    scim_token_expires_at: datetime | None
    scim_base_url: str | None
    oidc_callback_url: str | None
    last_scim_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime
    scim_token: str | None = None


class IdentityProviderList(BaseModel):
    items: list[IdentityProviderOut]


class SsoProviderOut(BaseModel):
    provider_id: uuid.UUID
    display_name: str


class SsoProviderList(BaseModel):
    items: list[SsoProviderOut]


def _provider_out(
    row: EnterpriseIdentityProvider,
    *,
    scim_token: str | None = None,
) -> dict:
    result = {
        "provider_id": str(row.provider_id),
        "organization_id": str(row.tenant_id),
        "display_name": row.display_name,
        "issuer_url": row.issuer_url,
        "client_id": row.client_id,
        "token_endpoint_auth_method": row.token_endpoint_auth_method,
        "has_client_secret": row.client_secret_ref is not None,
        "subject_claim": row.subject_claim,
        "email_claim": row.email_claim,
        "display_name_claim": row.display_name_claim,
        "scopes": list(row.scopes),
        "status": row.status,
        "scim_token_generation": int(row.scim_token_generation),
        "scim_token_expires_at": row.scim_token_expires_at,
        "scim_base_url": _absolute_external_url(
            f"scim/v2/{row.provider_id}"
        ),
        "oidc_callback_url": _absolute_external_url(
            "api/v1/auth/sso/callback"
        ),
        "last_scim_sync_at": row.last_scim_sync_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if scim_token is not None:
        result["scim_token"] = scim_token
    return result


async def _require_manage_policy(
    *,
    request: Request,
    auth: AuthContext,
    service: AuthzService,
    organization_id: uuid.UUID,
) -> None:
    if auth.active_organization_id != str(organization_id):
        raise HTTPException(404, "organization_not_found")
    await authorize_resource(
        request=request,
        auth=auth,
        service=service,
        resource=ResourceRef(
            ResourceType.ORGANIZATION,
            str(organization_id),
            str(organization_id),
        ),
        action=Action.MANAGE_POLICY,
    )


async def _config_audit(
    session: AsyncSession,
    *,
    request: Request,
    auth: AuthContext,
    provider: EnterpriseIdentityProvider,
    operation: str,
) -> None:
    await record_audit(
        session,
        action=audit_actions.ENTERPRISE_IDENTITY_CONFIG_CHANGE,
        actor_user_id=auth.user_id,
        actor_email=auth.email,
        target_type=audit_actions.TARGET_ENTERPRISE_IDENTITY,
        target_id=str(provider.provider_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={"operation": operation, "status": provider.status},
    )


@router.get(
    "/api/v1/organizations/{organization_id}/identity-providers",
    response_model=IdentityProviderList,
)
async def list_identity_providers(
    organization_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> dict:
    await _require_manage_policy(
        request=request,
        auth=auth,
        service=service,
        organization_id=organization_id,
    )
    rows = await EnterpriseIdentityRepo(session).list_providers(organization_id)
    return {"items": [_provider_out(row) for row in rows]}


@router.post(
    "/api/v1/organizations/{organization_id}/identity-providers",
    status_code=201,
    response_model=IdentityProviderOut,
)
async def create_identity_provider(
    organization_id: uuid.UUID,
    body: IdentityProviderCreate,
    request: Request,
    auth: AuthContext = Depends(current_user),
    _step_up: AuthContext = Depends(require_recent_step_up),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> dict:
    await _require_manage_policy(
        request=request,
        auth=auth,
        service=service,
        organization_id=organization_id,
    )
    organization = await session.get(Organization, organization_id)
    if organization is None or organization.kind != "business":
        raise HTTPException(409, "enterprise_identity_requires_business_organization")
    if _absolute_external_url("api/v1/auth/sso/callback") is None:
        raise HTTPException(409, "enterprise_identity_public_url_required")
    metadata = await discover_oidc(body.issuer_url)
    provided_client_secret = (
        body.client_secret.get_secret_value()
        if body.client_secret is not None else ""
    )
    token_auth_method = body.token_endpoint_auth_method or (
        "client_secret_basic" if provided_client_secret else "none"
    )
    if token_auth_method not in metadata.token_endpoint_auth_methods_supported:
        raise HTTPException(422, "oidc_token_auth_method_unsupported")
    if token_auth_method == "none" and provided_client_secret:
        raise HTTPException(422, "oidc_public_client_secret_forbidden")
    if token_auth_method != "none" and not provided_client_secret:
        raise HTTPException(422, "oidc_client_secret_required")
    provider_id = uuid.uuid4()
    client_secret_ref = None
    if provided_client_secret:
        client_secret_ref = await secret_service().put_text(
            session,
            tenant_id=organization_id,
            purpose="oidc_client_secret",
            resource_type="enterprise_identity_provider",
            resource_id=provider_id,
            plaintext=provided_client_secret,
        )
    raw_scim_token, scim_token_hash = _new_scim_token()
    provider = EnterpriseIdentityProvider(
        provider_id=provider_id,
        tenant_id=organization_id,
        organization_slug=organization.slug,
        display_name=body.display_name,
        issuer_url=metadata.issuer,
        client_id=body.client_id.strip(),
        token_endpoint_auth_method=token_auth_method,
        client_secret_ref=client_secret_ref,
        subject_claim=body.subject_claim,
        email_claim=body.email_claim,
        display_name_claim=body.display_name_claim,
        scopes=body.scopes,
        status="active",
        scim_token_hash=scim_token_hash,
        scim_token_expires_at=_now() + timedelta(days=body.scim_token_ttl_days),
        created_by=uuid.UUID(auth.user_id),
    )
    session.add(provider)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "identity_provider_issuer_already_configured") from exc
    await _config_audit(
        session,
        request=request,
        auth=auth,
        provider=provider,
        operation="create",
    )
    return _provider_out(provider, scim_token=raw_scim_token)


@router.patch(
    "/api/v1/organizations/{organization_id}/identity-providers/{provider_id}",
    response_model=IdentityProviderOut,
)
async def update_identity_provider(
    organization_id: uuid.UUID,
    provider_id: uuid.UUID,
    body: IdentityProviderPatch,
    request: Request,
    auth: AuthContext = Depends(current_user),
    _step_up: AuthContext = Depends(require_recent_step_up),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> dict:
    await _require_manage_policy(
        request=request,
        auth=auth,
        service=service,
        organization_id=organization_id,
    )
    repo = EnterpriseIdentityRepo(session)
    provider = await repo.get_provider(provider_id, tenant_id=organization_id)
    if provider is None:
        raise HTTPException(404, "identity_provider_not_found")
    if body.display_name is not None:
        provider.display_name = " ".join(body.display_name.split())
    if body.status is not None:
        provider.status = body.status
    for field in ("subject_claim", "email_claim", "display_name_claim"):
        value = getattr(body, field)
        if value is not None:
            setattr(provider, field, value)
    if body.scopes is not None:
        try:
            provider.scopes = _validated_scopes(body.scopes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    next_auth_method = (
        body.token_endpoint_auth_method
        if body.token_endpoint_auth_method is not None
        else provider.token_endpoint_auth_method
    )
    next_secret = (
        body.client_secret.get_secret_value()
        if body.client_secret is not None else None
    )
    has_next_secret = bool(next_secret) or (
        provider.client_secret_ref is not None
        and body.client_secret is None
        and next_auth_method != "none"
    )
    if next_auth_method == "none" and next_secret:
        raise HTTPException(422, "oidc_public_client_secret_forbidden")
    if next_auth_method != "none" and not has_next_secret:
        raise HTTPException(422, "oidc_client_secret_required")
    provider.token_endpoint_auth_method = next_auth_method
    if body.client_secret is not None:
        plaintext = body.client_secret.get_secret_value()
        if not plaintext:
            raise HTTPException(422, "oidc_client_secret_must_not_be_empty")
        previous_ref = provider.client_secret_ref
        next_secret_version = int((await session.execute(
            select(func.coalesce(func.max(EncryptedSecret.version), 0)).where(
                EncryptedSecret.tenant_id == organization_id,
                EncryptedSecret.purpose == "oidc_client_secret",
                EncryptedSecret.resource_type == "enterprise_identity_provider",
                EncryptedSecret.resource_id == str(provider_id),
            )
        )).scalar_one()) + 1
        provider.client_secret_ref = await secret_service().put_text(
            session,
            tenant_id=organization_id,
            purpose="oidc_client_secret",
            resource_type="enterprise_identity_provider",
            resource_id=provider_id,
            plaintext=plaintext,
            version=next_secret_version,
        )
        if previous_ref is not None:
            await secret_service().destroy(
                session,
                secret_ref=previous_ref,
                tenant_id=organization_id,
            )
    elif next_auth_method == "none" and provider.client_secret_ref is not None:
        previous_ref = provider.client_secret_ref
        provider.client_secret_ref = None
        await secret_service().destroy(
            session,
            secret_ref=previous_ref,
            tenant_id=organization_id,
        )
    provider.updated_at = _now()
    if provider.status == "disabled":
        directory_user_ids = select(EnterpriseDirectoryUser.user_id).where(
            EnterpriseDirectoryUser.provider_id == provider.provider_id,
        )
        await session.execute(
            delete(Session).where(
                Session.user_id.in_(directory_user_ids),
                Session.active_organization_id == organization_id,
            )
        )
    await session.flush()
    await _config_audit(
        session,
        request=request,
        auth=auth,
        provider=provider,
        operation="update",
    )
    return _provider_out(provider)


@router.post(
    "/api/v1/organizations/{organization_id}/identity-providers/"
    "{provider_id}/scim-token",
    response_model=IdentityProviderOut,
)
async def rotate_scim_token(
    organization_id: uuid.UUID,
    provider_id: uuid.UUID,
    body: ScimTokenRotate,
    request: Request,
    auth: AuthContext = Depends(current_user),
    _step_up: AuthContext = Depends(require_recent_step_up),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> dict:
    await _require_manage_policy(
        request=request,
        auth=auth,
        service=service,
        organization_id=organization_id,
    )
    provider = await EnterpriseIdentityRepo(session).get_provider(
        provider_id,
        tenant_id=organization_id,
    )
    if provider is None:
        raise HTTPException(404, "identity_provider_not_found")
    raw, digest = _new_scim_token()
    provider.scim_token_hash = digest
    provider.scim_token_generation += 1
    provider.scim_token_expires_at = _now() + timedelta(days=body.ttl_days)
    provider.updated_at = _now()
    await _config_audit(
        session,
        request=request,
        auth=auth,
        provider=provider,
        operation="rotate_scim_token",
    )
    return _provider_out(provider, scim_token=raw)


@router.get(
    "/api/v1/auth/sso/organizations/{organization_slug}/providers",
    response_model=SsoProviderList,
)
async def discover_organization_sso(
    organization_slug: str,
    request: Request,
) -> dict:
    _require_sso_login_enabled()
    await _limit_sso_request(request, "discovery")
    # Return an empty list for unknown organizations to avoid a separate
    # existence oracle. Provider identifiers are not credentials.
    async with session_scope() as session:
        providers = list((await session.execute(
            select(EnterpriseIdentityProvider).where(
                EnterpriseIdentityProvider.organization_slug
                == organization_slug.strip().lower(),
                EnterpriseIdentityProvider.status == "active",
            )
        )).scalars())
    return {
        "items": [
            {
                "provider_id": str(provider.provider_id),
                "display_name": provider.display_name,
            }
            for provider in providers
        ]
    }


@router.get(
    "/api/v1/auth/sso/providers/{provider_id}/start",
    status_code=302,
    response_class=RedirectResponse,
)
async def start_sso_login(
    provider_id: uuid.UUID,
    request: Request,
    return_to: str = Query(default="/", max_length=2048),
) -> RedirectResponse:
    _require_sso_login_enabled()
    await _limit_sso_request(request, "start")
    if not config.web_session_cookie_enabled:
        raise HTTPException(409, "sso_cookie_sessions_required")
    try:
        safe_return_to = validate_return_to(return_to)
    except OidcError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with session_scope() as lookup_session:
        provider = await EnterpriseIdentityRepo(lookup_session).get_provider(
            provider_id,
            active_only=True,
        )
    if provider is None:
        raise HTTPException(404, "sso_provider_not_found")
    try:
        async with session_scope(tenant_id=str(provider.tenant_id)) as session:
            current = await EnterpriseIdentityRepo(session).get_provider(
                provider_id,
                tenant_id=provider.tenant_id,
                active_only=True,
            )
            if current is None:
                raise HTTPException(404, "sso_provider_not_found")
            _, authorization_url = await create_login_transaction(
                session,
                provider=current,
                return_to=safe_return_to,
            )
    except OidcError as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(authorization_url, status_code=302)


@router.get(
    "/api/v1/auth/sso/callback",
    status_code=303,
    response_class=RedirectResponse,
)
async def complete_sso_login(
    request: Request,
    state: str = Query(min_length=32, max_length=512),
    code: str | None = Query(default=None, max_length=8192),
    error: str | None = Query(default=None, max_length=256),
) -> Response:
    _require_sso_login_enabled()
    await _limit_sso_request(request, "callback")
    state_digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    tenant_id: uuid.UUID | None = None
    provider_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    actor_email = ""
    try:
        async with session_scope() as lookup_session:
            transaction = await EnterpriseIdentityRepo(
                lookup_session
            ).get_login_transaction(state_digest)
            if transaction is None:
                raise OidcError("oidc_transaction_missing")
            tenant_id = transaction.tenant_id
            provider_id = transaction.provider_id

        # Consume state and destroy its verifier/nonce before external token
        # exchange. A code or state can therefore be attempted only once even
        # if the IdP or network later fails.
        async with session_scope(tenant_id=str(tenant_id)) as session:
            transaction = await EnterpriseIdentityRepo(
                session
            ).get_login_transaction(state_digest)
            if transaction is None:
                raise OidcError("oidc_transaction_missing")
            bundle = await resolve_transaction_bundle(session, transaction)
            return_to = transaction.return_to
            transaction_secret_ref = transaction.secret_ref
            await session.delete(transaction)
            await secret_service().destroy(
                session,
                secret_ref=transaction_secret_ref,
                tenant_id=tenant_id,
            )
        if error or not code:
            raise OidcError("oidc_authorization_failed")

        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = EnterpriseIdentityRepo(session)
            provider = await repo.get_provider(
                provider_id,
                tenant_id=tenant_id,
                active_only=True,
            )
            if provider is None:
                raise OidcError("oidc_provider_disabled")
            id_token, metadata = await exchange_code(
                session,
                provider=provider,
                code=code,
                bundle=bundle,
            )
            claims = await validate_id_token(
                id_token=id_token,
                metadata=metadata,
                provider=provider,
                nonce=bundle.nonce,
            )
            subject, claimed_email, _ = mapped_identity_claims(provider, claims)
            directory_user = await repo.directory_user_by_external_id(
                provider.provider_id,
                subject,
            )
            if directory_user is None or not directory_user.active:
                raise OidcError("oidc_user_not_provisioned")
            membership = (await session.execute(
                select(OrgMembership).where(
                    OrgMembership.user_id == directory_user.user_id,
                    OrgMembership.tenant_id == tenant_id,
                    OrgMembership.status == "active",
                    OrgMembership.source == "scim",
                    OrgMembership.directory_provider_id == provider.provider_id,
                )
            )).scalar_one_or_none()
            user = await AuthRepo(session).get_user(directory_user.user_id)
            if membership is None or user is None or user.status != "active":
                raise OidcError("oidc_user_not_provisioned")
            actor_user_id = user.user_id
            actor_email = user.email or claimed_email
            raw_session, token_hash = new_token()
            raw_csrf, csrf_hash = new_token()
            session_row = await AuthRepo(session).create_session(
                token_hash,
                user.user_id,
                tenant_id,
                _now() + _SESSION_TTL,
                audience="web",
                csrf_token_hash=csrf_hash,
                active_organization_id=tenant_id,
                authentication_strength="oauth",
            )

        response = RedirectResponse(return_to, status_code=303)
        set_session_cookies(
            response,
            audience="web",
            raw_session=raw_session,
            raw_csrf=raw_csrf,
            max_age=int(_SESSION_TTL.total_seconds()),
        )
        await record_auth_audit(
            action=audit_actions.AUTH_SSO_LOGIN_SUCCESS,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            tenant_id=tenant_id,
            outcome="success",
            audit_ctx=extract_request_audit_context(request),
            meta={
                "provider_id": str(provider_id),
                "session_id": str(session_row.session_id),
            },
        )
        return response
    except OidcError as exc:
        await record_auth_audit(
            action=audit_actions.AUTH_SSO_LOGIN_FAILURE,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            tenant_id=tenant_id,
            outcome="failure",
            audit_ctx=extract_request_audit_context(request),
            meta={
                "provider_id": str(provider_id) if provider_id else None,
                "reason_code": str(exc),
            },
        )
        raise HTTPException(400, str(exc)) from exc


__all__ = ["router"]

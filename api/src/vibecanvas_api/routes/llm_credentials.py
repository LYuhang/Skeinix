"""API Management Center routes for LLM credentials.

A private, tenant-scoped store of the user's own "bring your own key" LLM
configs. Centralizes what PromptNode today stores INLINE + plaintext in
``node_config.custom_model_config``. PromptNode / agent integration is a LATER
phase — this file only ships the store + its data layer.

THE SECRECY CONTRACT (mirrors how ``routes/mcp_servers.py`` scrubs its bearer
token) is enforced by three out-shapes:

  * ``GET ""``            → ``list[CredentialPublicOut]`` (id/name/description/
    provider only — the surface the PromptNode/agent picker will consume).
  * ``GET /{id}``         → ``CredentialOwnerOut`` (owner edit view: adds
    model_name/api_url + ``api_key_set`` flag; NO plaintext key).
  * ``POST ""`` create / ``PUT /{id}`` update → ``CredentialOwnerOut`` (the
    plaintext key is never echoed back).
  * ``DELETE /{id}``      → 204, soft delete.

Route ordering: ``/{id}/reveal`` is a sub-path of ``/{id}`` so FastAPI matches
it fine regardless of order, but list (``""``) is registered before the
``/{id}`` catch-alls per the MCP file's convention.

Audit: reversible key material is created, rotated and destroyed only through
``SecretService``, which records ``secret.create`` / ``secret.destroy`` in the
same transaction. There is no plaintext reveal route. Metadata authorization
mutations are separately captured by the durable OpenFGA mutation ledger.

Trust boundary (G4b): ``CredentialCreate`` / ``CredentialUpdate`` are
``extra='forbid'`` so a client cannot smuggle tenant_id / user_id / id /
enabled. Identity columns come exclusively from ``AuthContext``.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.auth.deps import (
    AuthContext,
    current_user,
    require_recent_step_up,
    tenant_db,
)
from vibecanvas_api.authorization.dependencies import (
    authorize_resource,
    context_for_auth,
    get_authz_service,
    mutation_coordinator_for_request,
    principal_for_auth,
)
from vibecanvas_api.authorization.projection import (
    apply_committed_structural_mutations,
    enqueue_structural_delta,
    resource_root_edges,
)
from vibecanvas_api.authorization.service import (
    AuthzService,
    batch_resource_decisions,
)
from vibecanvas_api.authorization.types import (
    Action,
    AuthorizedResource,
    ConsistencyPreference,
    Decision,
    ResourceRef,
    ResourceType,
)
from vibecanvas_api.schemas.access import access_from_decision
from vibecanvas_api.schemas.llm_credentials import (
    CredentialCreate,
    CredentialOwnerOut,
    CredentialPublicOut,
    CredentialUpdate,
)
from vibecanvas_api.security.secret_service import secret_service
from vibecanvas_api.services.llm_connection_secrets import (
    hydrate_llm_connection_credentials,
    store_llm_connection_credentials,
)
from vibecanvas_api.storage.repo_llm_credentials import LlmCredentialsRepo

router = APIRouter(prefix="/api/v1/llm-credentials", tags=["llm-credentials"])


# --------------------------------------------------------------- serializers


def _public_out(
    row: dict,
    decision: Decision | None = None,
) -> CredentialPublicOut:
    """List / public projection. NEVER touches model_name / api_url /
    api_key."""
    return CredentialPublicOut(
        id=str(row["id"]),
        name=row["name"],
        description=row.get("description"),
        provider=row["provider"],
        runtime_scope=row.get("runtime_scope") or "langchain",
        model_context_tokens=row.get("model_context_tokens"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        access=access_from_decision(decision) if decision else None,
    )


def _owner_out(
    row: dict,
    decision: Decision | None = None,
) -> CredentialOwnerOut:
    """Owner edit projection: non-secret config + ``api_key_set`` flag.
    The plaintext key is NEVER serialized here."""
    return CredentialOwnerOut(
        id=str(row["id"]),
        name=row["name"],
        description=row.get("description"),
        provider=row["provider"],
        runtime_scope=row.get("runtime_scope") or "langchain",
        model_name=row["model_name"],
        model_context_tokens=row.get("model_context_tokens"),
        api_url=row.get("api_url"),
        proxy=row.get("proxy"),
        api_key_set=bool(row.get("secret_ref")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        access=access_from_decision(decision) if decision else None,
    )


def _credential_resource(
    ctx: AuthContext,
    credential_id: uuid.UUID | str,
) -> ResourceRef:
    return ResourceRef(
        ResourceType.LLM_CREDENTIAL,
        str(credential_id),
        ctx.active_organization_id,
    )


async def _authorize_credential(
    *,
    request: Request,
    ctx: AuthContext,
    service: AuthzService,
    credential_id: uuid.UUID | str,
    action: Action,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> AuthorizedResource:
    resource = _credential_resource(ctx, credential_id)
    decision = await service.check(
        principal_for_auth(ctx),
        action,
        resource,
        context_for_auth(ctx, request, consistency=consistency),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="credential not found",
        )
    return AuthorizedResource(resource=resource, decision=decision)


async def _rebind_request_organization(
    session: AsyncSession,
    ctx: AuthContext,
) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :organization_id, true)"),
        {"organization_id": ctx.active_organization_id},
    )


# --------------------------------------------------------------------- create


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: CredentialCreate,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    _step_up: AuthContext = Depends(require_recent_step_up),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> CredentialOwnerOut:
    """Create a credential for the current tenant. Returns the owner view —
    the plaintext key is NOT echoed back. The partial UNIQUE index on
    ``(tenant_id, name) WHERE deleted_at IS NULL`` surfaces as an
    IntegrityError → 409."""
    await authorize_resource(
        request=request,
        auth=ctx,
        service=service,
        resource=ResourceRef(
            ResourceType.ORGANIZATION,
            ctx.active_organization_id,
            ctx.active_organization_id,
        ),
        action=Action.CREATE,
    )
    repo = LlmCredentialsRepo(session)
    cid = uuid.uuid4()
    secret_ref = await secret_service().put_text(
        session,
        tenant_id=ctx.active_organization_id,
        purpose="llm_api_key",
        resource_type="llm_credential",
        resource_id=cid,
        plaintext=body.api_key,
    )
    stored_api_url, stored_proxy, connection_secret_ref = (
        await store_llm_connection_credentials(
            session,
            tenant_id=ctx.active_organization_id,
            credential_id=cid,
            api_url=body.api_url,
            proxy=body.proxy,
            version=1,
        )
    )
    fields = dict(
        id=cid,
        tenant_id=uuid.UUID(ctx.tenant_id),
        user_id=uuid.UUID(ctx.user_id),
        name=body.name,
        description=body.description,
        provider=body.provider,
        runtime_scope=body.runtime_scope,
        model_name=body.model_name,
        model_context_tokens=body.model_context_tokens,
        api_url=stored_api_url,
        proxy=stored_proxy,
        connection_secret_ref=connection_secret_ref,
        connection_secret_version=1,
        secret_ref=secret_ref,
        secret_version=1,
        enabled=True,
    )
    try:
        await repo.insert(**fields)
        await session.flush()  # surface IntegrityError NOW, not at request end
    except IntegrityError as e:
        # Only the live-name partial UNIQUE index is a user conflict.  Do not
        # disguise FK/CHECK/NOT NULL failures as "already exists": those are
        # storage/security-cutover defects that operators must be able to see.
        if getattr(e.orig, "sqlstate", None) != "23505":
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a credential with this name already exists",
        ) from e
    coordinator = mutation_coordinator_for_request(
        request,
        ctx.active_organization_id,
    )
    mutation_ids = await enqueue_structural_delta(
        session=session,
        coordinator=coordinator,
        actor_type="user",
        actor_id=ctx.user_id,
        before=frozenset(),
        after=resource_root_edges(
            organization_id=ctx.active_organization_id,
            object_type="llm_credential",
            object_id=str(cid),
            owner_relation="owner",
            owner_type="user",
            owner_id=ctx.user_id,
        ),
        operation_id=f"llm-credential:{cid}:create",
        source="llm-credential-create",
    )
    await session.commit()
    await apply_committed_structural_mutations(coordinator, mutation_ids)
    await _rebind_request_organization(session, ctx)
    row = await repo.get(cid)
    authorized = await _authorize_credential(
        request=request,
        ctx=ctx,
        service=service,
        credential_id=cid,
        action=Action.MANAGE_SECRET,
        consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
    )
    return _owner_out(row, authorized.decision)


# ----------------------------------------------------------------------- list


@router.get("")
async def list_credentials(
    request: Request,
    ctx: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> list[CredentialPublicOut]:
    """List every live credential for the current tenant. PUBLIC projection
    only (id/name/description/provider) — this is the surface PromptNode /
    agent pickers consume; it MUST NOT leak model_name / api_url / api_key."""
    repo = LlmCredentialsRepo(session)
    context = context_for_auth(ctx, request)
    authorized_ids = await service.list_authorized_ids(
        principal_for_auth(ctx),
        Action.VIEW_METADATA,
        ResourceType.LLM_CREDENTIAL,
        context,
    )
    rows = await repo.list_authorized(authorized_ids)
    resources = [
        _credential_resource(ctx, row["id"]) for row in rows
    ]
    decisions = await batch_resource_decisions(
        service,
        principal=principal_for_auth(ctx),
        resources=resources,
        context=context,
    )
    return [
        _public_out(row, decisions[resource])
        for row, resource in zip(rows, resources, strict=True)
    ]


# ----------------------------------------------------------------------- get


@router.get("/{credential_id}")
async def get_credential(
    credential_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> CredentialOwnerOut:
    """Owner edit view of one credential. 404 if missing / soft-deleted /
    cross-tenant (RLS hides the last equivalently to non-existence). Returns
    model_name + api_url but NO plaintext key."""
    authorized = await _authorize_credential(
        request=request,
        ctx=ctx,
        service=service,
        credential_id=credential_id,
        action=Action.MANAGE_SECRET,
    )
    repo = LlmCredentialsRepo(session)
    row = await repo.get(credential_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="credential not found",
        )
    return _owner_out(row, authorized.decision)


# ---------------------------------------------------------------------- update


@router.put("/{credential_id}")
async def update_credential(
    credential_id: uuid.UUID,
    body: CredentialUpdate,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
) -> CredentialOwnerOut:
    """Partial update. ``api_key`` omitted OR empty => keep the existing key
    (we never blank a secret on a metadata-only edit). 404 if missing.
    Returns the owner view (no plaintext key)."""
    sensitive_fields = {
        "api_key", "api_url", "proxy", "provider", "runtime_scope",
    }
    if sensitive_fields & body.model_fields_set:
        await require_recent_step_up(ctx)
    authorized = await _authorize_credential(
        request=request,
        ctx=ctx,
        service=service,
        credential_id=credential_id,
        action=Action.MANAGE_SECRET,
        consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
    )
    repo = LlmCredentialsRepo(session)
    existing = await repo.get(credential_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="credential not found",
        )

    fields = body.model_dump(exclude_unset=True)
    new_api_key = fields.pop("api_key", None)
    previous_secret_ref = existing.get("secret_ref")
    previous_connection_secret_ref = existing.get("connection_secret_ref")
    connection_changed = any(key in fields for key in ("api_url", "proxy"))
    hydrated_existing = await hydrate_llm_connection_credentials(
        session, existing
    )
    if new_api_key:
        next_version = int(existing.get("secret_version") or 0) + 1
        fields.update(
            secret_ref=await secret_service().put_text(
                session,
                tenant_id=ctx.active_organization_id,
                purpose="llm_api_key",
                resource_type="llm_credential",
                resource_id=credential_id,
                plaintext=new_api_key,
                version=next_version,
            ),
            secret_version=next_version,
        )

    if connection_changed:
        full_api_url = fields.get("api_url", hydrated_existing.get("api_url"))
        full_proxy = fields.get("proxy", hydrated_existing.get("proxy"))
        # An edit form may submit the exact redacted value it received. Keep
        # the existing full value rather than rotating the mask into storage.
        if full_api_url == existing.get("api_url"):
            full_api_url = hydrated_existing.get("api_url")
        if full_proxy == existing.get("proxy"):
            full_proxy = hydrated_existing.get("proxy")
        next_connection_version = int(
            existing.get("connection_secret_version") or 0
        ) + 1
        (
            fields["api_url"],
            fields["proxy"],
            fields["connection_secret_ref"],
        ) = await store_llm_connection_credentials(
            session,
            tenant_id=ctx.active_organization_id,
            credential_id=credential_id,
            api_url=full_api_url,
            proxy=full_proxy,
            version=next_connection_version,
        )
        fields["connection_secret_version"] = next_connection_version

    if fields:
        try:
            await repo.update(credential_id, **fields)
            await session.flush()
            if new_api_key and previous_secret_ref:
                await secret_service().destroy(
                    session,
                    secret_ref=previous_secret_ref,
                    tenant_id=ctx.active_organization_id,
                )
            if connection_changed and previous_connection_secret_ref:
                await secret_service().destroy(
                    session,
                    secret_ref=previous_connection_secret_ref,
                    tenant_id=ctx.active_organization_id,
                )
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a credential with this name already exists",
            ) from e

    row = await repo.get(credential_id)
    return _owner_out(row, authorized.decision)


# ---------------------------------------------------------------------- delete


@router.delete(
    "/{credential_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_credential(
    credential_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    _step_up: AuthContext = Depends(require_recent_step_up),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
):
    """Soft-delete a credential (sets ``deleted_at`` + ``enabled=FALSE``).
    Idempotent — deleting an already-deleted / cross-tenant row 404s (RLS +
    the WHERE filter hide it from the pre-load lookup)."""
    await _authorize_credential(
        request=request,
        ctx=ctx,
        service=service,
        credential_id=credential_id,
        action=Action.DELETE,
        consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
    )
    repo = LlmCredentialsRepo(session)
    existing = await repo.get(credential_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="credential not found",
        )
    await repo.soft_delete(credential_id)
    if existing.get("secret_ref"):
        await secret_service().destroy(
            session,
            secret_ref=existing["secret_ref"],
            tenant_id=ctx.active_organization_id,
        )
    if existing.get("connection_secret_ref"):
        await secret_service().destroy(
            session,
            secret_ref=existing["connection_secret_ref"],
            tenant_id=ctx.active_organization_id,
        )
    coordinator = mutation_coordinator_for_request(
        request,
        ctx.active_organization_id,
    )
    mutation_ids = await enqueue_structural_delta(
        session=session,
        coordinator=coordinator,
        actor_type="user",
        actor_id=ctx.user_id,
        before=resource_root_edges(
            organization_id=ctx.active_organization_id,
            object_type="llm_credential",
            object_id=str(credential_id),
            owner_relation="owner",
            owner_type="user",
            owner_id=str(existing["user_id"]),
        ),
        after=frozenset(),
        operation_id=f"llm-credential:{credential_id}:delete",
        source="llm-credential-delete",
    )
    await session.commit()
    await apply_committed_structural_mutations(coordinator, mutation_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

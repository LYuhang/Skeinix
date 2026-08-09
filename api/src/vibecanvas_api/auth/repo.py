"""AuthRepo — CRUD over the 5 auth tables. Auth tables have no RLS, so
this repo runs without a tenant context (register/login happen before
any tenant is known)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import uuid

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.storage.models import (
    AuthIdentity,
    PasswordResetToken,
    Session,
    SessionExchangeCode,
    Tenant,
    User,
)
from vibecanvas_api.storage.models_org import Organization, OrgMembership
from vibecanvas_api.security.identity_protection import (
    decrypt_user_profile,
    encrypt_account_deletion_email,
    encrypt_provider_uid,
    encrypt_user_profile,
    identity_lookup_digest,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AuthUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    display_name: str
    status: str
    created_at: datetime
    updated_at: datetime


class AuthRepo:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def register(self, email: str, password_hash: str,
                       display_name: str = "") -> AuthUser:
        """Create a personal organization, owner, and identity atomically."""
        tenant = Tenant(name="Personal workspace")
        self._s.add(tenant)
        await self._s.flush()                    # populate tenant_id
        # Organization tables are FORCE-RLS protected. Registration starts
        # before a tenant is known, so bind the just-created personal
        # organization to this transaction before inserting its rows.
        await self._s.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.tenant_id)},
        )
        user_id = uuid.uuid4()
        user = User(
            user_id=user_id,
            tenant_id=tenant.tenant_id,
            email_sentinel=f"redacted-{user_id}@invalid.local",
            display_name_sentinel="",
        )
        self._s.add(user)
        await self._s.flush()
        resolved_display_name = display_name or email
        profile = await encrypt_user_profile(
            self._s,
            user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            email=email,
            display_name=resolved_display_name,
        )
        user.profile_ciphertext = profile.ciphertext
        user.profile_nonce = profile.nonce
        user.profile_key_id = profile.key_id
        self._s.add(Organization(
            tenant_id=tenant.tenant_id,
            kind="personal",
            slug=f"org-{str(tenant.tenant_id).replace('-', '')}",
            name="Personal workspace",
            created_by=user.user_id,
        ))
        self._s.add(OrgMembership(
            user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            org_role="owner",
            status="active",
        ))
        identity_id = uuid.uuid4()
        provider_uid = await encrypt_provider_uid(
            self._s,
            identity_id=identity_id,
            user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            provider="password",
            provider_uid=email,
        )
        self._s.add(AuthIdentity(
            identity_id=identity_id,
            user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            provider="password",
            provider_uid_sentinel=f"redacted-{identity_id}",
            provider_uid_lookup_hash=identity_lookup_digest("password", email),
            provider_uid_ciphertext=provider_uid.ciphertext,
            provider_uid_nonce=provider_uid.nonce,
            provider_uid_key_id=provider_uid.key_id,
            secret=password_hash,
        ))
        await self._s.flush()
        return AuthUser(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            email=email.strip(),
            display_name=resolved_display_name,
            status=user.status,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    async def find_identity(self, provider: str,
                            provider_uid: str) -> AuthIdentity | None:
        normalized_provider = provider.strip().lower()
        q = select(AuthIdentity).where(
            AuthIdentity.provider == normalized_provider,
            AuthIdentity.provider_uid_lookup_hash
            == identity_lookup_digest(normalized_provider, provider_uid),
        )
        return (await self._s.execute(q)).scalar_one_or_none()

    async def get_user(self, user_id) -> AuthUser | None:
        row = await self._s.get(User, user_id)
        if row is None:
            return None
        profile = await decrypt_user_profile(self._s, row)
        return AuthUser(
            user_id=row.user_id,
            tenant_id=row.tenant_id,
            email=profile.email,
            display_name=profile.display_name,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create_session(
        self,
        token_hash: str,
        user_id,
        tenant_id,
        expires_at: datetime,
        *,
        audience: str = "web",
        parent_session_id=None,
        csrf_token_hash: str | None = None,
        active_organization_id=None,
        authentication_strength: str = "password",
        step_up_expires_at: datetime | None = None,
        privileged_access_request_id=None,
    ) -> Session:
        organization_id = uuid.UUID(str(tenant_id))
        session = Session(
            token_hash=token_hash,
            user_id=user_id,
            tenant_id=organization_id,
            active_organization_id=uuid.UUID(str(
                active_organization_id or organization_id
            )),
            expires_at=expires_at,
            audience=audience,
            parent_session_id=parent_session_id,
            csrf_token_hash=csrf_token_hash,
            authentication_strength=authentication_strength,
            step_up_expires_at=step_up_expires_at,
            privileged_access_request_id=privileged_access_request_id,
        )
        self._s.add(session)
        await self._s.flush()
        return session

    async def resolve_session(self, token_hash: str) -> Session | None:
        s = await self._s.get(Session, token_hash)
        if s is None or s.expires_at <= _now():
            return None
        return s

    async def get_session_by_id(
        self,
        session_id,
        *,
        user_id=None,
    ) -> Session | None:
        query = select(Session).where(Session.session_id == session_id)
        if user_id is not None:
            query = query.where(Session.user_id == user_id)
        return (await self._s.execute(query)).scalar_one_or_none()

    async def list_user_sessions(self, user_id) -> list[Session]:
        return list(
            (
                await self._s.execute(
                    select(Session)
                    .where(
                        Session.user_id == user_id,
                        Session.expires_at > _now(),
                    )
                    .order_by(Session.last_seen_at.desc(), Session.created_at.desc())
                )
            ).scalars()
        )

    async def rotate_session_token(
        self,
        *,
        session_id,
        user_id,
        token_hash: str,
        csrf_token_hash: str,
    ) -> Session | None:
        rotated_hash = (
            await self._s.execute(
                update(Session)
                .where(
                    Session.session_id == session_id,
                    Session.user_id == user_id,
                    Session.expires_at > _now(),
                )
                .values(
                    token_hash=token_hash,
                    csrf_token_hash=csrf_token_hash,
                    generation=Session.generation + 1,
                    last_seen_at=_now(),
                )
                .returning(Session.token_hash)
            )
        ).scalar_one_or_none()
        if rotated_hash is None:
            return None
        await self._s.flush()
        return await self._s.get(Session, rotated_hash)

    async def touch_session(self, token_hash: str) -> None:
        """Throttled last_seen update — only if stale > 5 min."""
        s = await self._s.get(Session, token_hash)
        if s and (_now() - s.last_seen_at).total_seconds() > 300:
            s.last_seen_at = _now()
            await self._s.flush()

    async def get_membership(
        self,
        *,
        user_id,
        organization_id,
    ) -> OrgMembership | None:
        """Resolve one membership using the authenticated-user RLS seam."""
        await self._s.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        return (
            await self._s.execute(
                select(OrgMembership).where(
                    OrgMembership.user_id == user_id,
                    OrgMembership.tenant_id == organization_id,
                )
            )
        ).scalar_one_or_none()

    async def list_memberships(self, user_id) -> list[OrgMembership]:
        """List only the caller's own non-revoked organization memberships."""
        await self._s.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        return list(
            (
                await self._s.execute(
                    select(OrgMembership)
                    .where(
                        OrgMembership.user_id == user_id,
                        OrgMembership.status != "revoked",
                    )
                    .order_by(OrgMembership.created_at, OrgMembership.membership_id)
                )
            ).scalars()
        )

    async def list_organizations_for_user(self, user_id) -> list[dict]:
        """Return organization metadata without relaxing organization RLS.

        Membership discovery is authorized by ``app.user_id``. Organization
        rows are then loaded one scope at a time by changing the transaction-
        local ``app.tenant_id``. No client-provided organization header is used.
        """
        memberships = await self.list_memberships(user_id)
        result: list[dict] = []
        for membership in memberships:
            await self._s.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(membership.tenant_id)},
            )
            organization = await self._s.get(Organization, membership.tenant_id)
            if organization is None:
                continue
            result.append({
                "organization_id": str(organization.tenant_id),
                "kind": organization.kind,
                "slug": organization.slug,
                "name": organization.name,
                "membership_id": str(membership.membership_id),
                "role": membership.org_role,
                "status": membership.status,
            })
        return result

    async def create_business_organization(
        self,
        *,
        user_id,
        name: str,
        slug: str,
    ) -> tuple[Organization, OrgMembership]:
        """Create a business organization and its first owner atomically."""
        tenant = Tenant(name=name)
        self._s.add(tenant)
        await self._s.flush()
        await self._s.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.tenant_id)},
        )
        organization = Organization(
            tenant_id=tenant.tenant_id,
            kind="business",
            slug=slug,
            name=name,
            created_by=user_id,
        )
        self._s.add(organization)
        membership = OrgMembership(
            user_id=user_id,
            tenant_id=tenant.tenant_id,
            org_role="owner",
            status="active",
        )
        self._s.add(membership)
        await self._s.flush()
        return organization, membership

    async def switch_active_organization(
        self,
        *,
        session_id,
        user_id,
        organization_id,
        token_hash: str | None = None,
        csrf_token_hash: str | None = None,
    ) -> Session | None:
        """Atomically validate membership and rotate Session generation."""
        membership = await self.get_membership(
            user_id=user_id,
            organization_id=organization_id,
        )
        if membership is None or membership.status != "active":
            return None
        values = {
            "tenant_id": organization_id,
            "active_organization_id": organization_id,
            "generation": Session.generation + 1,
            "last_seen_at": _now(),
            # Step-up is intentionally scoped to the active organization
            # context and must be repeated after a tenant boundary switch.
            "step_up_expires_at": None,
        }
        if token_hash is not None:
            values["token_hash"] = token_hash
        if csrf_token_hash is not None:
            values["csrf_token_hash"] = csrf_token_hash
        statement = (
            update(Session)
            .where(
                Session.session_id == session_id,
                Session.user_id == user_id,
                Session.expires_at > _now(),
            )
            .values(**values)
            .returning(Session.token_hash)
        )
        token_hash = (await self._s.execute(statement)).scalar_one_or_none()
        if token_hash is None:
            return None
        await self._s.flush()
        return await self._s.get(Session, token_hash)

    async def delete_session(self, token_hash: str) -> None:
        await self._s.execute(
            delete(Session).where(Session.token_hash == token_hash))
        await self._s.flush()

    async def delete_session_by_id(self, *, session_id, user_id) -> bool:
        deleted = (
            await self._s.execute(
                delete(Session)
                .where(
                    Session.session_id == session_id,
                    Session.user_id == user_id,
                )
                .returning(Session.session_id)
            )
        ).scalar_one_or_none()
        await self._s.flush()
        return deleted is not None

    async def delete_derived_sessions(self, parent_session_id) -> None:
        await self._s.execute(
            delete(Session).where(
                Session.parent_session_id == parent_session_id
            )
        )
        await self._s.flush()

    async def create_session_exchange_code(
        self,
        *,
        code_hash: str,
        parent_session_id,
        user_id,
        tenant_id,
        expires_at: datetime,
    ) -> None:
        self._s.add(SessionExchangeCode(
            code_hash=code_hash,
            parent_session_id=parent_session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            audience="extension",
            expires_at=expires_at,
        ))
        await self._s.flush()

    async def consume_session_exchange_code(
        self,
        code_hash: str,
    ) -> tuple[dict, Session] | None:
        row = (
            await self._s.execute(
                text(
                    "DELETE FROM session_exchange_codes AS code "
                    "USING sessions AS parent "
                    "WHERE code.code_hash = :code_hash "
                    "AND code.expires_at > now() "
                    "AND code.parent_session_id = parent.session_id "
                    "AND parent.expires_at > now() "
                    "AND parent.user_id = code.user_id "
                    "AND parent.tenant_id = code.tenant_id "
                    "RETURNING code.parent_session_id, code.user_id, "
                    "code.tenant_id, code.audience"
                ),
                {"code_hash": code_hash},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        parent = await self.get_session_by_id(row["parent_session_id"])
        if parent is None:  # deleted between DELETE and SELECT; same txn only
            return None
        await self._s.flush()
        return dict(row), parent

    async def delete_user_sessions(self, user_id) -> None:
        await self._s.execute(
            delete(Session).where(Session.user_id == user_id))
        await self._s.flush()

    async def request_account_deletion(
        self, *, user_id, tenant_id, email: str, purge_after: datetime,
    ) -> None:
        """Mark the account pending deletion and invalidate sessions.

        The physical purge is intentionally outside the user request path. A
        later admin sweeper will consume ``account_deletion_requests``.
        """
        await self._s.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(status="pending_deletion"))
        encrypted_email = await encrypt_account_deletion_email(
            self._s,
            user_id=uuid.UUID(str(user_id)),
            tenant_id=uuid.UUID(str(tenant_id)),
            email=email,
        )
        request_id = (await self._s.execute(
            text(
                "INSERT INTO account_deletion_requests "
                "(user_id, tenant_id, email_snapshot, "
                "email_snapshot_ciphertext, email_snapshot_nonce, "
                "email_snapshot_key_id, purge_after) "
                "VALUES (:user_id, :tenant_id, '', :ciphertext, :nonce, "
                ":key_id, :purge_after) "
                "ON CONFLICT (user_id) WHERE status IN ('pending','purging','failed') "
                "DO UPDATE SET status = 'pending', requested_at = now(), "
                "purge_after = EXCLUDED.purge_after, cancelled_at = NULL, "
                "purging_at = NULL, purged_at = NULL, last_error = NULL, "
                "email_snapshot='', "
                "email_snapshot_ciphertext=EXCLUDED.email_snapshot_ciphertext, "
                "email_snapshot_nonce=EXCLUDED.email_snapshot_nonce, "
                "email_snapshot_key_id=EXCLUDED.email_snapshot_key_id "
                "RETURNING id"
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "ciphertext": encrypted_email.ciphertext,
                "nonce": encrypted_email.nonce,
                "key_id": encrypted_email.key_id,
                "purge_after": purge_after,
            },
        )).scalar_one()
        await self._s.execute(
            text(
                "INSERT INTO data_purge_jobs "
                "(deletion_request_id, user_id, tenant_id, status, available_at) "
                "VALUES (:request_id, :user_id, :tenant_id, 'queued', :purge_after) "
                "ON CONFLICT (deletion_request_id) DO UPDATE SET "
                "status = 'queued', available_at = EXCLUDED.available_at, "
                "current_phase = NULL, completed_phases = '[]'::jsonb, "
                "lease_expires_at = NULL, last_error_code = NULL, "
                "last_error_message = NULL, completed_at = NULL, updated_at = now()"
            ),
            {
                "request_id": request_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "purge_after": purge_after,
            },
        )
        await self.delete_user_sessions(user_id)

    async def cancel_account_deletion(self, user_id) -> None:
        await self._s.execute(
            text(
                "UPDATE account_deletion_requests "
                "SET status = 'cancelled', cancelled_at = now() "
                "WHERE user_id = :user_id AND status IN ('pending','failed')"
            ),
            {"user_id": user_id},
        )
        await self._s.execute(
            text(
                "UPDATE data_purge_jobs SET status = 'cancelled', "
                "lease_expires_at = NULL, updated_at = now() "
                "WHERE user_id = :user_id AND status IN ('queued','failed')"
            ),
            {"user_id": user_id},
        )
        await self._s.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(status="active"))
        await self._s.flush()

    async def set_user_status(self, user_id, status: str) -> None:
        await self._s.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(status=status))
        await self._s.flush()

    async def create_reset_token(self, token_hash: str, user_id,
                                 expires_at: datetime) -> None:
        self._s.add(PasswordResetToken(token_hash=token_hash, user_id=user_id,
                                       expires_at=expires_at))
        await self._s.flush()

    async def consume_reset_token(self, token_hash: str) -> PasswordResetToken | None:
        t = await self._s.get(PasswordResetToken, token_hash)
        if t is None or t.used_at is not None or t.expires_at <= _now():
            return None
        t.used_at = _now()
        await self._s.flush()
        return t

    async def update_password(self, user_id, new_hash: str) -> None:
        await self._s.execute(
            update(AuthIdentity)
            .where(AuthIdentity.user_id == user_id,
                   AuthIdentity.provider == "password")
            .values(secret=new_hash))
        await self._s.flush()

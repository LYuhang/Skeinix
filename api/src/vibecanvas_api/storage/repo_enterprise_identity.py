"""Persistence helpers for enterprise identity providers and directory users."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.enterprise_identity import (
    directory_lookup_digest,
)
from vibecanvas_api.storage.models_enterprise_identity import (
    EnterpriseDirectoryUser,
    EnterpriseIdentityProvider,
    OidcLoginTransaction,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EnterpriseIdentityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_provider(
        self,
        provider_id: uuid.UUID,
        *,
        tenant_id: uuid.UUID | None = None,
        active_only: bool = False,
    ) -> EnterpriseIdentityProvider | None:
        query = select(EnterpriseIdentityProvider).where(
            EnterpriseIdentityProvider.provider_id == provider_id,
        )
        if tenant_id is not None:
            query = query.where(
                EnterpriseIdentityProvider.tenant_id == tenant_id,
            )
        if active_only:
            query = query.where(EnterpriseIdentityProvider.status == "active")
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_providers(
        self,
        tenant_id: uuid.UUID,
    ) -> list[EnterpriseIdentityProvider]:
        return list((await self.session.execute(
            select(EnterpriseIdentityProvider)
            .where(EnterpriseIdentityProvider.tenant_id == tenant_id)
            .order_by(
                EnterpriseIdentityProvider.created_at,
                EnterpriseIdentityProvider.provider_id,
            )
        )).scalars())

    async def provider_for_issuer(
        self,
        *,
        tenant_id: uuid.UUID,
        issuer_url: str,
    ) -> EnterpriseIdentityProvider | None:
        return (await self.session.execute(
            select(EnterpriseIdentityProvider).where(
                EnterpriseIdentityProvider.tenant_id == tenant_id,
                EnterpriseIdentityProvider.issuer_url == issuer_url,
            )
        )).scalar_one_or_none()

    async def directory_user_by_id(
        self,
        provider_id: uuid.UUID,
        directory_user_id: uuid.UUID,
    ) -> EnterpriseDirectoryUser | None:
        return (await self.session.execute(
            select(EnterpriseDirectoryUser).where(
                EnterpriseDirectoryUser.provider_id == provider_id,
                EnterpriseDirectoryUser.directory_user_id == directory_user_id,
            )
        )).scalar_one_or_none()

    async def directory_user_by_external_id(
        self,
        provider_id: uuid.UUID,
        external_id: str,
    ) -> EnterpriseDirectoryUser | None:
        digest = directory_lookup_digest(
            provider_id,
            "external_id",
            external_id,
        )
        return (await self.session.execute(
            select(EnterpriseDirectoryUser).where(
                EnterpriseDirectoryUser.provider_id == provider_id,
                EnterpriseDirectoryUser.external_id_lookup_hash == digest,
            )
        )).scalar_one_or_none()

    async def directory_user_by_user_name(
        self,
        provider_id: uuid.UUID,
        user_name: str,
    ) -> EnterpriseDirectoryUser | None:
        digest = directory_lookup_digest(
            provider_id,
            "user_name",
            user_name,
            casefold=True,
        )
        return (await self.session.execute(
            select(EnterpriseDirectoryUser).where(
                EnterpriseDirectoryUser.provider_id == provider_id,
                EnterpriseDirectoryUser.user_name_lookup_hash == digest,
            )
        )).scalar_one_or_none()

    async def list_directory_users(
        self,
        provider_id: uuid.UUID,
    ) -> list[EnterpriseDirectoryUser]:
        return list((await self.session.execute(
            select(EnterpriseDirectoryUser)
            .where(EnterpriseDirectoryUser.provider_id == provider_id)
            .order_by(
                EnterpriseDirectoryUser.created_at,
                EnterpriseDirectoryUser.directory_user_id,
            )
        )).scalars())

    async def get_login_transaction(
        self,
        state_hash: str,
        *,
        active_only: bool = True,
    ) -> OidcLoginTransaction | None:
        query = select(OidcLoginTransaction).where(
            OidcLoginTransaction.state_hash == state_hash,
        )
        if active_only:
            query = query.where(OidcLoginTransaction.expires_at > _now())
        return (await self.session.execute(query)).scalar_one_or_none()

    async def consume_login_transaction(
        self,
        state_hash: str,
    ) -> OidcLoginTransaction | None:
        row = await self.get_login_transaction(state_hash)
        if row is None:
            return None
        await self.session.delete(row)
        await self.session.flush()
        return row

    async def delete_expired_login_transactions(self) -> int:
        result = await self.session.execute(
            delete(OidcLoginTransaction)
            .where(OidcLoginTransaction.expires_at <= _now())
            .returning(OidcLoginTransaction.state_hash)
        )
        return len(result.scalars().all())


__all__ = ["EnterpriseIdentityRepo"]

"""Idempotently encrypt legacy plaintext business secrets in host context."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import uuid

from sqlalchemy import text

from vibecanvas_api.security.secret_service import secret_service
from vibecanvas_api.security.migration_legacy_crypto import (
    decrypt_migration_value,
)
from vibecanvas_api.services.mcp_connection_secrets import (
    store_connection_credentials,
)
from vibecanvas_api.services.llm_connection_secrets import (
    store_llm_connection_credentials,
)
from vibecanvas_api.storage.db import get_admin_engine, session_scope


@dataclass(frozen=True, slots=True)
class LegacyLlmSecret:
    credential_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyMcpBearer:
    server_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyMcpOAuthConnection:
    server_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyMcpOAuthTransaction:
    state_hash: str
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyDeploymentHmac:
    deployment_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyMcpConnectionCredentials:
    server_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyLlmConnectionCredentials:
    credential_id: uuid.UUID
    tenant_id: uuid.UUID


async def _candidate_ids(limit: int) -> list[LegacyLlmSecret]:
    async with get_admin_engine().connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, tenant_id FROM llm_credentials "
                    "WHERE api_key IS NOT NULL AND secret_ref IS NULL "
                    "ORDER BY tenant_id, id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyLlmSecret(
            credential_id=row["id"],
            tenant_id=row["tenant_id"],
        )
        for row in rows
    ]


async def _encrypt_one(candidate: LegacyLlmSecret) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT api_key FROM llm_credentials "
                    "WHERE id = :id AND tenant_id = :tenant_id "
                    "AND api_key IS NOT NULL AND secret_ref IS NULL "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {
                    "id": candidate.credential_id,
                    "tenant_id": candidate.tenant_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return False
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=candidate.tenant_id,
            purpose="llm_api_key",
            resource_type="llm_credential",
            resource_id=candidate.credential_id,
            plaintext=row["api_key"],
        )
        await session.execute(
            text(
                "UPDATE llm_credentials SET api_key = NULL, "
                "secret_ref = :secret_ref, secret_version = 1, "
                "updated_at = now() WHERE id = :id AND secret_ref IS NULL"
            ),
            {"secret_ref": secret_ref, "id": candidate.credential_id},
        )
        return True


async def migrate_legacy_llm_secrets(*, batch_size: int = 100) -> int:
    migrated = 0
    while candidates := await _candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_one(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    return migrated


async def _mcp_candidate_ids(limit: int) -> list[LegacyMcpBearer]:
    async with get_admin_engine().connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, tenant_id FROM mcp_servers "
                    "WHERE auth_secret_ref IS NULL "
                    "AND auth_config->>'type' = 'bearer' "
                    "AND coalesce(auth_config->>'token', '') <> '' "
                    "ORDER BY tenant_id, id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyMcpBearer(server_id=row["id"], tenant_id=row["tenant_id"])
        for row in rows
    ]


async def _encrypt_mcp_bearer(candidate: LegacyMcpBearer) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT auth_config->>'token' AS token FROM mcp_servers "
                    "WHERE id = :id AND tenant_id = :tenant_id "
                    "AND auth_secret_ref IS NULL "
                    "AND auth_config->>'type' = 'bearer' "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {"id": candidate.server_id, "tenant_id": candidate.tenant_id},
            )
        ).mappings().one_or_none()
        if row is None or not row["token"]:
            return False
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=candidate.tenant_id,
            purpose="mcp_bearer_token",
            resource_type="mcp_installation",
            resource_id=candidate.server_id,
            plaintext=row["token"],
        )
        await session.execute(
            text(
                "UPDATE mcp_servers SET "
                "auth_config = '{\"type\":\"bearer\"}'::jsonb, "
                "auth_secret_ref = :secret_ref, auth_secret_version = 1, "
                "updated_at = now() WHERE id = :id AND auth_secret_ref IS NULL"
            ),
            {"secret_ref": secret_ref, "id": candidate.server_id},
        )
        return True


async def migrate_legacy_mcp_bearers(*, batch_size: int = 100) -> int:
    migrated = 0
    while candidates := await _mcp_candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_mcp_bearer(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    # Historical ``none`` auth records used ``{"type":"none","token":""}``
    # as their structural default. There is no secret to encrypt, but the
    # credential-shaped key must still be removed before the strict schema
    # guard can guarantee that no token field survives. Never normalize a
    # non-empty value here: those remain fail-closed unless migrated above.
    async with get_admin_engine().begin() as connection:
        await connection.execute(
            text(
                "UPDATE mcp_servers SET auth_config = auth_config - 'token', "
                "updated_at = now() WHERE auth_config ? 'token' "
                "AND coalesce(auth_config->>'token', '') = ''"
            )
        )
    return migrated


async def _oauth_connection_candidate_ids(
    limit: int,
) -> list[LegacyMcpOAuthConnection]:
    async with get_admin_engine().connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT server_id, tenant_id FROM mcp_oauth_connections "
                    "WHERE secret_ref IS NULL "
                    "AND access_token_encrypted IS NOT NULL "
                    "ORDER BY tenant_id, server_id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyMcpOAuthConnection(
            server_id=row["server_id"], tenant_id=row["tenant_id"]
        )
        for row in rows
    ]


async def _encrypt_oauth_connection(
    candidate: LegacyMcpOAuthConnection,
) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT client_secret_encrypted, access_token_encrypted, "
                    "refresh_token_encrypted FROM mcp_oauth_connections "
                    "WHERE server_id = :server_id AND tenant_id = :tenant_id "
                    "AND secret_ref IS NULL "
                    "AND access_token_encrypted IS NOT NULL "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {
                    "server_id": candidate.server_id,
                    "tenant_id": candidate.tenant_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return False
        access_token = decrypt_migration_value(row["access_token_encrypted"])
        if not access_token:
            return False
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=candidate.tenant_id,
            purpose="mcp_oauth_tokens",
            resource_type="mcp_installation",
            resource_id=candidate.server_id,
            plaintext=json.dumps(
                {
                    "client_secret": decrypt_migration_value(
                        row["client_secret_encrypted"]
                    ),
                    "access_token": access_token,
                    "refresh_token": decrypt_migration_value(
                        row["refresh_token_encrypted"]
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        await session.execute(
            text(
                "UPDATE mcp_oauth_connections SET "
                "secret_ref = :secret_ref, secret_version = 1, "
                "client_secret_encrypted = NULL, "
                "access_token_encrypted = NULL, "
                "refresh_token_encrypted = NULL, updated_at = now() "
                "WHERE server_id = :server_id AND secret_ref IS NULL"
            ),
            {"secret_ref": secret_ref, "server_id": candidate.server_id},
        )
        return True


async def migrate_legacy_mcp_oauth_connections(
    *, batch_size: int = 100,
) -> int:
    migrated = 0
    while candidates := await _oauth_connection_candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_oauth_connection(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    return migrated


async def _oauth_transaction_candidate_ids(
    limit: int,
) -> list[LegacyMcpOAuthTransaction]:
    async with get_admin_engine().connect() as connection:
        await connection.execute(
            text(
                "DELETE FROM mcp_oauth_transactions "
                "WHERE expires_at <= now() AND secret_ref IS NULL"
            )
        )
        await connection.commit()
        rows = (
            await connection.execute(
                text(
                    "SELECT state_hash, tenant_id FROM mcp_oauth_transactions "
                    "WHERE secret_ref IS NULL "
                    "AND code_verifier_encrypted IS NOT NULL "
                    "AND expires_at > now() "
                    "ORDER BY tenant_id, state_hash LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyMcpOAuthTransaction(
            state_hash=row["state_hash"], tenant_id=row["tenant_id"]
        )
        for row in rows
    ]


async def _encrypt_oauth_transaction(
    candidate: LegacyMcpOAuthTransaction,
) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT code_verifier_encrypted, client_secret_encrypted "
                    "FROM mcp_oauth_transactions "
                    "WHERE state_hash = :state_hash "
                    "AND tenant_id = :tenant_id AND secret_ref IS NULL "
                    "AND expires_at > now() FOR UPDATE SKIP LOCKED"
                ),
                {
                    "state_hash": candidate.state_hash,
                    "tenant_id": candidate.tenant_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return False
        verifier = decrypt_migration_value(row["code_verifier_encrypted"])
        if not verifier:
            return False
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=candidate.tenant_id,
            purpose="mcp_oauth_transaction",
            resource_type="mcp_oauth_transaction",
            resource_id=candidate.state_hash,
            plaintext=json.dumps(
                {
                    "code_verifier": verifier,
                    "client_secret": decrypt_migration_value(
                        row["client_secret_encrypted"]
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        await session.execute(
            text(
                "UPDATE mcp_oauth_transactions SET "
                "secret_ref = :secret_ref, secret_version = 1, "
                "code_verifier_encrypted = NULL, "
                "client_secret_encrypted = NULL "
                "WHERE state_hash = :state_hash AND secret_ref IS NULL"
            ),
            {"secret_ref": secret_ref, "state_hash": candidate.state_hash},
        )
        return True


async def migrate_legacy_mcp_oauth_transactions(
    *, batch_size: int = 100,
) -> int:
    migrated = 0
    while candidates := await _oauth_transaction_candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_oauth_transaction(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    return migrated


async def _deployment_hmac_candidate_ids(
    limit: int,
) -> list[LegacyDeploymentHmac]:
    async with get_admin_engine().connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, tenant_id FROM deployments "
                    "WHERE trigger_type = 'webhook' "
                    "AND hmac_secret_ref IS NULL "
                    "AND coalesce(hmac_secret, '') <> '' "
                    "ORDER BY tenant_id, id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyDeploymentHmac(
            deployment_id=row["id"], tenant_id=row["tenant_id"]
        )
        for row in rows
    ]


async def _encrypt_deployment_hmac(
    candidate: LegacyDeploymentHmac,
) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT hmac_secret FROM deployments "
                    "WHERE id = :id AND tenant_id = :tenant_id "
                    "AND trigger_type = 'webhook' "
                    "AND hmac_secret_ref IS NULL "
                    "AND coalesce(hmac_secret, '') <> '' "
                    "FOR UPDATE SKIP LOCKED"
                ),
                {
                    "id": candidate.deployment_id,
                    "tenant_id": candidate.tenant_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return False
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=candidate.tenant_id,
            purpose="deployment_webhook_hmac",
            resource_type="deployment",
            resource_id=candidate.deployment_id,
            plaintext=row["hmac_secret"],
        )
        await session.execute(
            text(
                "UPDATE deployments SET hmac_secret = NULL, "
                "hmac_secret_ref = :secret_ref, hmac_secret_version = 1, "
                "updated_at = now() WHERE id = :id "
                "AND hmac_secret_ref IS NULL"
            ),
            {"secret_ref": secret_ref, "id": candidate.deployment_id},
        )
        return True


async def migrate_legacy_deployment_hmacs(
    *, batch_size: int = 100,
) -> int:
    migrated = 0
    while candidates := await _deployment_hmac_candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_deployment_hmac(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    return migrated


_MCP_CONNECTION_SECRET_PREDICATE = """
(
    (jsonb_typeof(connection_config->'headers') = 'object'
     AND connection_config->'headers' <> '{}'::jsonb)
 OR (jsonb_typeof(connection_config->'env') = 'object'
     AND connection_config->'env' <> '{}'::jsonb)
 OR position('?' in endpoint) > 0
 OR position('?' in coalesce(connection_config->>'url', '')) > 0
)
"""


async def _mcp_connection_candidate_ids(
    limit: int,
) -> list[LegacyMcpConnectionCredentials]:
    async with get_admin_engine().connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, tenant_id FROM mcp_servers "
                    "WHERE connection_secret_ref IS NULL AND "
                    + _MCP_CONNECTION_SECRET_PREDICATE
                    + " ORDER BY tenant_id, id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyMcpConnectionCredentials(
            server_id=row["id"], tenant_id=row["tenant_id"]
        )
        for row in rows
    ]


async def _encrypt_mcp_connection_credentials(
    candidate: LegacyMcpConnectionCredentials,
) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT endpoint, connection_config FROM mcp_servers "
                    "WHERE id = :id AND tenant_id = :tenant_id "
                    "AND connection_secret_ref IS NULL AND "
                    + _MCP_CONNECTION_SECRET_PREDICATE
                    + " FOR UPDATE SKIP LOCKED"
                ),
                {"id": candidate.server_id, "tenant_id": candidate.tenant_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return False
        endpoint, connection_config, secret_ref = (
            await store_connection_credentials(
                session,
                tenant_id=candidate.tenant_id,
                server_id=candidate.server_id,
                endpoint=row["endpoint"],
                connection_config=row["connection_config"],
                version=1,
            )
        )
        if secret_ref is None:
            return False
        await session.execute(
            text(
                "UPDATE mcp_servers SET endpoint = :endpoint, "
                "connection_config = CAST(:connection_config AS jsonb), "
                "connection_secret_ref = :secret_ref, "
                "connection_secret_version = 1, updated_at = now() "
                "WHERE id = :id AND connection_secret_ref IS NULL"
            ),
            {
                "endpoint": endpoint,
                "connection_config": json.dumps(connection_config),
                "secret_ref": secret_ref,
                "id": candidate.server_id,
            },
        )
        return True


async def migrate_legacy_mcp_connection_credentials(
    *, batch_size: int = 100,
) -> int:
    migrated = 0
    while candidates := await _mcp_connection_candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_mcp_connection_credentials(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    return migrated


_LLM_CONNECTION_SECRET_PREDICATE = """
(
    position('?' in coalesce(api_url, '')) > 0
 OR position('?' in coalesce(proxy, '')) > 0
 OR coalesce(api_url, '') ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@'
 OR coalesce(proxy, '') ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@'
)
"""


async def _llm_connection_candidate_ids(
    limit: int,
) -> list[LegacyLlmConnectionCredentials]:
    async with get_admin_engine().connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, tenant_id FROM llm_credentials "
                    "WHERE connection_secret_ref IS NULL AND "
                    + _LLM_CONNECTION_SECRET_PREDICATE
                    + " ORDER BY tenant_id, id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return [
        LegacyLlmConnectionCredentials(
            credential_id=row["id"], tenant_id=row["tenant_id"]
        )
        for row in rows
    ]


async def _encrypt_llm_connection_credentials(
    candidate: LegacyLlmConnectionCredentials,
) -> bool:
    async with session_scope(tenant_id=str(candidate.tenant_id)) as session:
        row = (
            await session.execute(
                text(
                    "SELECT api_url, proxy FROM llm_credentials "
                    "WHERE id = :id AND tenant_id = :tenant_id "
                    "AND connection_secret_ref IS NULL AND "
                    + _LLM_CONNECTION_SECRET_PREDICATE
                    + " FOR UPDATE SKIP LOCKED"
                ),
                {
                    "id": candidate.credential_id,
                    "tenant_id": candidate.tenant_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return False
        api_url, proxy, secret_ref = await store_llm_connection_credentials(
            session,
            tenant_id=candidate.tenant_id,
            credential_id=candidate.credential_id,
            api_url=row["api_url"],
            proxy=row["proxy"],
            version=1,
        )
        if secret_ref is None:
            return False
        await session.execute(
            text(
                "UPDATE llm_credentials SET api_url = :api_url, "
                "proxy = :proxy, connection_secret_ref = :secret_ref, "
                "connection_secret_version = 1, updated_at = now() "
                "WHERE id = :id AND connection_secret_ref IS NULL"
            ),
            {
                "api_url": api_url,
                "proxy": proxy,
                "secret_ref": secret_ref,
                "id": candidate.credential_id,
            },
        )
        return True


async def migrate_legacy_llm_connection_credentials(
    *, batch_size: int = 100,
) -> int:
    migrated = 0
    while candidates := await _llm_connection_candidate_ids(batch_size):
        progress = 0
        for candidate in candidates:
            if await _encrypt_llm_connection_credentials(candidate):
                migrated += 1
                progress += 1
        if progress == 0:
            break
    return migrated


async def legacy_secret_count() -> int:
    async with get_admin_engine().connect() as connection:
        column_rows = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name FROM "
                    "information_schema.columns WHERE "
                    "table_schema=current_schema() AND table_name IN "
                    "('llm_credentials','mcp_oauth_connections',"
                    "'mcp_oauth_transactions','deployments')"
                )
            )
        ).all()
        columns = {(str(row[0]), str(row[1])) for row in column_rows}

        llm_checks = ["secret_ref IS NULL"]
        if ("llm_credentials", "api_key") in columns:
            llm_checks.append("api_key IS NOT NULL")
        oauth_connection_checks = ["secret_ref IS NULL"]
        for column in (
            "client_secret_encrypted",
            "access_token_encrypted",
            "refresh_token_encrypted",
        ):
            if ("mcp_oauth_connections", column) in columns:
                oauth_connection_checks.append(f"{column} IS NOT NULL")
        oauth_transaction_checks = ["secret_ref IS NULL"]
        for column in ("code_verifier_encrypted", "client_secret_encrypted"):
            if ("mcp_oauth_transactions", column) in columns:
                oauth_transaction_checks.append(f"{column} IS NOT NULL")
        deployment_checks = ["hmac_secret_ref IS NULL"]
        if ("deployments", "hmac_secret") in columns:
            deployment_checks.append("hmac_secret IS NOT NULL")

        query = (
            "SELECT (SELECT count(*) FROM llm_credentials WHERE "
            + " OR ".join(llm_checks)
            + ") + (SELECT count(*) FROM mcp_servers WHERE "
            "auth_config->>'type'='bearer' AND (auth_secret_ref IS NULL OR "
            "coalesce(auth_config->>'token','')<>'')) + "
            "(SELECT count(*) FROM mcp_oauth_connections WHERE "
            + " OR ".join(oauth_connection_checks)
            + ") + (SELECT count(*) FROM mcp_oauth_transactions WHERE "
            "expires_at > now() AND ("
            + " OR ".join(oauth_transaction_checks)
            + ")) + (SELECT count(*) FROM deployments WHERE "
            "trigger_type='webhook' AND ("
            + " OR ".join(deployment_checks)
            + ")) + (SELECT count(*) FROM mcp_servers WHERE "
            "connection_secret_ref IS NULL AND "
            + _MCP_CONNECTION_SECRET_PREDICATE
            + ") + (SELECT count(*) FROM llm_credentials WHERE "
            "connection_secret_ref IS NULL AND "
            + _LLM_CONNECTION_SECRET_PREDICATE
            + ")"
        )
        return int((await connection.execute(text(query))).scalar_one())


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        remaining = await legacy_secret_count()
        print(f"legacy_secrets={remaining}")
        return 1 if remaining else 0
    llm_migrated = await migrate_legacy_llm_secrets(
        batch_size=max(1, args.batch_size)
    )
    mcp_migrated = await migrate_legacy_mcp_bearers(
        batch_size=max(1, args.batch_size)
    )
    oauth_connections_migrated = await migrate_legacy_mcp_oauth_connections(
        batch_size=max(1, args.batch_size)
    )
    oauth_transactions_migrated = await migrate_legacy_mcp_oauth_transactions(
        batch_size=max(1, args.batch_size)
    )
    deployment_hmacs_migrated = await migrate_legacy_deployment_hmacs(
        batch_size=max(1, args.batch_size)
    )
    mcp_connection_credentials_migrated = (
        await migrate_legacy_mcp_connection_credentials(
            batch_size=max(1, args.batch_size)
        )
    )
    llm_connection_credentials_migrated = (
        await migrate_legacy_llm_connection_credentials(
            batch_size=max(1, args.batch_size)
        )
    )
    remaining = await legacy_secret_count()
    print(
        f"llm_migrated={llm_migrated} mcp_migrated={mcp_migrated} "
        f"oauth_connections_migrated={oauth_connections_migrated} "
        f"oauth_transactions_migrated={oauth_transactions_migrated} "
        f"deployment_hmacs_migrated={deployment_hmacs_migrated} "
        f"mcp_connection_credentials_migrated="
        f"{mcp_connection_credentials_migrated} "
        f"llm_connection_credentials_migrated="
        f"{llm_connection_credentials_migrated} "
        f"remaining={remaining}"
    )
    return 1 if remaining else 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()

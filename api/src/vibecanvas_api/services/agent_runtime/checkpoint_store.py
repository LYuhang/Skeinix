"""Host-owned durable store for sandbox Runtime checkpoints.

The sandbox never receives this module's DSN.  It serializes LangGraph values
into opaque typed bytes, while this host adapter validates the capability scope
and performs only bounded SQL operations.  In particular, the host never calls
``loads_typed`` on data supplied by a sandbox.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass
import json
import re
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from vibecanvas_api.config import config
from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.storage.db import session_scope


logger = structlog.get_logger(__name__)

_ALLOWED_SERIALIZATION_TYPES = frozenset(
    {"null", "bytes", "bytearray", "json", "msgpack"}
)
_REQUEST_ID_RE = re.compile(r"^state_[a-f0-9]{32}$")
_MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
_MAX_WRITE_BYTES = 8 * 1024 * 1024
_MAX_METADATA_INDEX_BYTES = 64 * 1024
_ENCRYPTED_SERIALIZATION_PREFIX = "vcenc1:"


class RuntimeStateProtocolError(ValueError):
    """A bounded, user-safe protocol rejection."""


@dataclass(frozen=True, slots=True)
class RuntimeStateScope:
    organization_id: str
    chat_id: str
    runtime_session_id: str
    thread_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("organization_id", self.organization_id),
            ("chat_id", self.chat_id),
            ("runtime_session_id", self.runtime_session_id),
            ("thread_id", self.thread_id),
        ):
            if not value or len(value) > 512 or "\x00" in value:
                raise RuntimeStateProtocolError(f"invalid runtime state {name}")


def _bounded_text(
    value: Any,
    name: str,
    *,
    max_length: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise RuntimeStateProtocolError(f"{name} must be a string")
    if (not value and not allow_empty) or len(value) > max_length or "\x00" in value:
        raise RuntimeStateProtocolError(f"invalid {name}")
    return value


def _decode_opaque(value: Any, *, max_bytes: int) -> tuple[str, bytes]:
    if not isinstance(value, dict):
        raise RuntimeStateProtocolError("runtime state value must be an object")
    serialization = _bounded_text(
        value.get("serialization"), "serialization", max_length=32
    )
    if serialization not in _ALLOWED_SERIALIZATION_TYPES:
        raise RuntimeStateProtocolError("unsupported runtime state serialization")
    encoded = value.get("data")
    if not isinstance(encoded, str):
        raise RuntimeStateProtocolError("runtime state payload must be base64")
    # Reject oversized base64 before allocating the decoded buffer.
    if len(encoded) > ((max_bytes + 2) // 3) * 4 + 4:
        raise RuntimeStateProtocolError("runtime state payload exceeds its limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RuntimeStateProtocolError("runtime state payload is not valid base64") from exc
    if len(raw) > max_bytes:
        raise RuntimeStateProtocolError("runtime state payload exceeds its limit")
    return serialization, raw


def _encode_opaque(serialization: str, raw: bytes) -> dict[str, str]:
    return {
        "serialization": serialization,
        "data": base64.b64encode(bytes(raw)).decode("ascii"),
    }


def _metadata_index(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeStateProtocolError("metadata_index must be an object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeStateProtocolError("metadata_index must contain JSON values") from exc
    if len(encoded) > _MAX_METADATA_INDEX_BYTES:
        raise RuntimeStateProtocolError("metadata_index exceeds its limit")
    return value


_SETUP_SQL = (
"""
CREATE TABLE IF NOT EXISTS vc_runtime_checkpoints (
    organization_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    runtime_session_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT NULL,
    checkpoint_serialization TEXT NOT NULL,
    checkpoint_payload BYTEA NOT NULL,
    metadata_serialization TEXT NOT NULL,
    metadata_payload BYTEA NOT NULL,
    metadata_index JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        organization_id, chat_id, runtime_session_id, thread_id,
        checkpoint_ns, checkpoint_id
    )
)
""",
"""
CREATE INDEX IF NOT EXISTS ix_vc_runtime_checkpoints_latest
ON vc_runtime_checkpoints (
    organization_id, chat_id, runtime_session_id, thread_id,
    checkpoint_ns, checkpoint_id DESC
)
""",
"""
CREATE TABLE IF NOT EXISTS vc_runtime_checkpoint_writes (
    organization_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    runtime_session_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    write_index INTEGER NOT NULL,
    channel TEXT NOT NULL,
    value_serialization TEXT NOT NULL,
    value_payload BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        organization_id, chat_id, runtime_session_id, thread_id,
        checkpoint_ns, checkpoint_id, task_id, task_path, write_index
    )
)
""",
"""
CREATE INDEX IF NOT EXISTS ix_vc_runtime_checkpoint_writes_lookup
ON vc_runtime_checkpoint_writes (
    organization_id, chat_id, runtime_session_id, thread_id,
    checkpoint_ns, checkpoint_id, task_id, write_index
)
""",
)


async def setup_runtime_state_schema(pool: AsyncConnectionPool) -> None:
    """Create broker-owned tables from a migration/maintenance connection."""
    async with pool.connection() as conn:
        for statement in _SETUP_SQL:
            await conn.execute(statement)


class LangChainCheckpointStore:
    """Runtime State Broker backend using the host-only Runtime DB role."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._pool: AsyncConnectionPool | None = None
        self._pool_lock = asyncio.Lock()

    def _conninfo(self) -> str:
        dsn = self._dsn or config.agent_runtime_database_url
        if not dsn:
            raise RuntimeStateProtocolError(
                "host Runtime State database is not configured"
            )
        return dsn.replace("+asyncpg", "")

    async def _get_pool(self) -> AsyncConnectionPool:
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            pool = AsyncConnectionPool(
                conninfo=self._conninfo(),
                open=False,
                min_size=1,
                max_size=max(1, int(config.database.checkpointer_pool_max_size)),
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
            )
            try:
                await pool.open(wait=True, timeout=15.0)
                async with pool.connection() as conn:
                    # Runtime identities deliberately have DML but no DDL or
                    # table-owner privileges.  Schema creation belongs to the
                    # one-shot migration workload; this read-only probe makes
                    # an incomplete deployment fail closed without asking the
                    # API process to regain schema privileges.
                    await conn.execute(
                        "SELECT 1 FROM vc_runtime_checkpoints LIMIT 0"
                    )
                    await conn.execute(
                        "SELECT 1 FROM vc_runtime_checkpoint_writes LIMIT 0"
                    )
            except Exception:
                with suppress(Exception):
                    await pool.close()
                raise
            self._pool = pool
            return pool

    async def purge_organization(
        self,
        organization_id: str,
        *,
        legacy_thread_ids: list[str] | None = None,
        chat_ids: list[str] | None = None,
    ) -> None:
        """Physically erase Runtime-owned state without exposing its DSN.

        The new broker tables have an explicit organization key. Legacy
        LangGraph tables are removed only by exact thread IDs or the bounded
        ``sub:{chat_id}:`` namespace derived from already-authorized Chat rows.
        """
        pool = await self._get_pool()
        threads = sorted({str(value) for value in (legacy_thread_ids or []) if value})
        chats = sorted({str(value) for value in (chat_ids or []) if value})
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM vc_runtime_checkpoint_writes "
                    "WHERE organization_id = %s",
                    (str(organization_id),),
                )
                await conn.execute(
                    "DELETE FROM vc_runtime_checkpoints "
                    "WHERE organization_id = %s",
                    (str(organization_id),),
                )
                tables = await (
                    await conn.execute(
                        "SELECT to_regclass('public.checkpoints') AS checkpoints, "
                        "to_regclass('public.checkpoint_blobs') AS blobs, "
                        "to_regclass('public.checkpoint_writes') AS writes"
                    )
                ).fetchone()
                if not tables:
                    return
                for thread_id in threads:
                    if tables.get("writes"):
                        await conn.execute(
                            "DELETE FROM checkpoint_writes WHERE thread_id = %s",
                            (thread_id,),
                        )
                    if tables.get("blobs"):
                        await conn.execute(
                            "DELETE FROM checkpoint_blobs WHERE thread_id = %s",
                            (thread_id,),
                        )
                    if tables.get("checkpoints"):
                        await conn.execute(
                            "DELETE FROM checkpoints WHERE thread_id = %s",
                            (thread_id,),
                        )
                for chat_id in chats:
                    pattern = f"sub:{chat_id}:%"
                    if tables.get("writes"):
                        await conn.execute(
                            "DELETE FROM checkpoint_writes WHERE thread_id LIKE %s",
                            (pattern,),
                        )
                    if tables.get("blobs"):
                        await conn.execute(
                            "DELETE FROM checkpoint_blobs WHERE thread_id LIKE %s",
                            (pattern,),
                        )
                    if tables.get("checkpoints"):
                        await conn.execute(
                            "DELETE FROM checkpoints WHERE thread_id LIKE %s",
                            (pattern,),
                        )

    async def purge_chats(
        self,
        organization_id: str,
        *,
        chat_ids: list[str],
        legacy_thread_ids: list[str] | None = None,
    ) -> None:
        """Erase selected user-owned Chat state without deleting an organization.

        Account deletion may retain organization-owned content. Runtime
        checkpoints remain identity-scoped, so only the deleted user's Chat
        rows are removed from those organizations.
        """
        chats = sorted({str(value) for value in chat_ids if value})
        threads = sorted({
            str(value) for value in (legacy_thread_ids or []) if value
        })
        if not chats and not threads:
            return
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.transaction():
                for chat_id in chats:
                    await conn.execute(
                        "DELETE FROM vc_runtime_checkpoint_writes "
                        "WHERE organization_id = %s AND chat_id = %s",
                        (str(organization_id), chat_id),
                    )
                    await conn.execute(
                        "DELETE FROM vc_runtime_checkpoints "
                        "WHERE organization_id = %s AND chat_id = %s",
                        (str(organization_id), chat_id),
                    )
                tables = await (
                    await conn.execute(
                        "SELECT to_regclass('public.checkpoints') AS checkpoints, "
                        "to_regclass('public.checkpoint_blobs') AS blobs, "
                        "to_regclass('public.checkpoint_writes') AS writes"
                    )
                ).fetchone()
                if not tables:
                    return
                exact_threads = set(threads)
                for chat_id in chats:
                    exact_threads.add(f"sub:{chat_id}:%")
                for thread_id in sorted(exact_threads):
                    operator = "LIKE" if thread_id.endswith("%") else "="
                    for table_name, available in (
                        ("checkpoint_writes", tables.get("writes")),
                        ("checkpoint_blobs", tables.get("blobs")),
                        ("checkpoints", tables.get("checkpoints")),
                    ):
                        if available:
                            await conn.execute(
                                f"DELETE FROM {table_name} WHERE thread_id {operator} %s",
                                (thread_id,),
                            )

    async def close(self) -> None:
        async with self._pool_lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            with suppress(Exception):
                await pool.close()

    async def backfill_encryption(self, *, batch_size: int = 100) -> int:
        """Encrypt pre-cutover broker rows in place, without deserialization.

        This is a deployment migration only. Runtime reads remain strict and
        never use it as a compatibility fallback.
        """
        limit = max(1, min(int(batch_size), 1000))
        migrated = 0
        pool = await self._get_pool()
        while True:
            async with pool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute(
                        """
                        SELECT * FROM vc_runtime_checkpoints
                        WHERE checkpoint_serialization NOT LIKE 'vcenc1:%%'
                           OR metadata_serialization NOT LIKE 'vcenc1:%%'
                        ORDER BY created_at
                        LIMIT %s FOR UPDATE SKIP LOCKED
                        """,
                        (limit,),
                    )
                    rows = await cursor.fetchall()
                    if not rows:
                        break
                    for row in rows:
                        scope = RuntimeStateScope(
                            organization_id=str(row["organization_id"]),
                            chat_id=str(row["chat_id"]),
                            runtime_session_id=str(row["runtime_session_id"]),
                            thread_id=str(row["thread_id"]),
                        )
                        namespace = str(row["checkpoint_ns"])
                        checkpoint_id = str(row["checkpoint_id"])
                        checkpoint_type = str(row["checkpoint_serialization"])
                        checkpoint_payload = bytes(row["checkpoint_payload"])
                        metadata_type = str(row["metadata_serialization"])
                        metadata_payload = bytes(row["metadata_payload"])
                        async with session_scope(
                            tenant_id=scope.organization_id
                        ) as key_session:
                            if not checkpoint_type.startswith(
                                _ENCRYPTED_SERIALIZATION_PREFIX
                            ):
                                checkpoint_type, checkpoint_payload = (
                                    await self._protect_payload(
                                        key_session,
                                        scope,
                                        namespace=namespace,
                                        checkpoint_id=checkpoint_id,
                                        suffix="checkpoint",
                                        serialization=checkpoint_type,
                                        raw=checkpoint_payload,
                                    )
                                )
                            if not metadata_type.startswith(
                                _ENCRYPTED_SERIALIZATION_PREFIX
                            ):
                                metadata_type, metadata_payload = (
                                    await self._protect_payload(
                                        key_session,
                                        scope,
                                        namespace=namespace,
                                        checkpoint_id=checkpoint_id,
                                        suffix="metadata",
                                        serialization=metadata_type,
                                        raw=metadata_payload,
                                    )
                                )
                        await conn.execute(
                            """
                            UPDATE vc_runtime_checkpoints
                               SET checkpoint_serialization=%s,
                                   checkpoint_payload=%s,
                                   metadata_serialization=%s,
                                   metadata_payload=%s,
                                   updated_at=clock_timestamp()
                             WHERE organization_id=%s AND chat_id=%s
                               AND runtime_session_id=%s AND thread_id=%s
                               AND checkpoint_ns=%s AND checkpoint_id=%s
                            """,
                            (
                                checkpoint_type,
                                checkpoint_payload,
                                metadata_type,
                                metadata_payload,
                                *self._scope_args(scope),
                                namespace,
                                checkpoint_id,
                            ),
                        )
                        migrated += 1

        while True:
            async with pool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute(
                        """
                        SELECT * FROM vc_runtime_checkpoint_writes
                        WHERE value_serialization NOT LIKE 'vcenc1:%%'
                        ORDER BY created_at
                        LIMIT %s FOR UPDATE SKIP LOCKED
                        """,
                        (limit,),
                    )
                    rows = await cursor.fetchall()
                    if not rows:
                        break
                    for row in rows:
                        scope = RuntimeStateScope(
                            organization_id=str(row["organization_id"]),
                            chat_id=str(row["chat_id"]),
                            runtime_session_id=str(row["runtime_session_id"]),
                            thread_id=str(row["thread_id"]),
                        )
                        namespace = str(row["checkpoint_ns"])
                        checkpoint_id = str(row["checkpoint_id"])
                        async with session_scope(
                            tenant_id=scope.organization_id
                        ) as key_session:
                            value_type, value_payload = await self._protect_payload(
                                key_session,
                                scope,
                                namespace=namespace,
                                checkpoint_id=checkpoint_id,
                                suffix=(
                                    f"write|{row['task_id']}|"
                                    f"{row.get('task_path') or ''}|"
                                    f"{row['write_index']}|{row['channel']}"
                                ),
                                serialization=str(row["value_serialization"]),
                                raw=bytes(row["value_payload"]),
                            )
                        await conn.execute(
                            """
                            UPDATE vc_runtime_checkpoint_writes
                               SET value_serialization=%s, value_payload=%s,
                                   updated_at=clock_timestamp()
                             WHERE organization_id=%s AND chat_id=%s
                               AND runtime_session_id=%s AND thread_id=%s
                               AND checkpoint_ns=%s AND checkpoint_id=%s
                               AND task_id=%s AND task_path=%s AND write_index=%s
                            """,
                            (
                                value_type,
                                value_payload,
                                *self._scope_args(scope),
                                namespace,
                                checkpoint_id,
                                str(row["task_id"]),
                                str(row.get("task_path") or ""),
                                int(row["write_index"]),
                            ),
                        )
                        migrated += 1
        return migrated

    async def plaintext_row_count(self) -> int:
        """Return the strict-cutover guard count for deployment tooling."""
        pool = await self._get_pool()
        async with pool.connection() as conn:
            checkpoints = await (
                await conn.execute(
                    """
                    SELECT count(*) AS count FROM vc_runtime_checkpoints
                    WHERE checkpoint_serialization NOT LIKE 'vcenc1:%'
                       OR metadata_serialization NOT LIKE 'vcenc1:%'
                    """
                )
            ).fetchone()
            writes = await (
                await conn.execute(
                    """
                    SELECT count(*) AS count FROM vc_runtime_checkpoint_writes
                    WHERE value_serialization NOT LIKE 'vcenc1:%'
                    """
                )
            ).fetchone()
        return int((checkpoints or {}).get("count", 0)) + int(
            (writes or {}).get("count", 0)
        )

    @staticmethod
    def _scope_args(scope: RuntimeStateScope) -> tuple[str, str, str, str]:
        return (
            scope.organization_id,
            scope.chat_id,
            scope.runtime_session_id,
            scope.thread_id,
        )

    @staticmethod
    def _record_id(
        scope: RuntimeStateScope,
        *,
        namespace: str,
        checkpoint_id: str,
        suffix: str,
    ) -> str:
        # Length-prefixing avoids ambiguous concatenation while keeping the
        # database identity independent from user-controlled serialization.
        values = (
            scope.runtime_session_id,
            scope.thread_id,
            namespace,
            checkpoint_id,
            suffix,
        )
        return "|".join(f"{len(value)}:{value}" for value in values)

    async def _protect_payload(
        self,
        session: AsyncSession,
        scope: RuntimeStateScope,
        *,
        namespace: str,
        checkpoint_id: str,
        suffix: str,
        serialization: str,
        raw: bytes,
    ) -> tuple[str, bytes]:
        encrypted = await content_encryption_service().encrypt_bytes(
            session,
            tenant_id=scope.organization_id,
            resource_type="chat",
            resource_id=scope.chat_id,
            purpose="runtime_checkpoint",
            record_id=self._record_id(
                scope,
                namespace=namespace,
                checkpoint_id=checkpoint_id,
                suffix=suffix,
            ),
            plaintext=raw,
        )
        envelope = json.dumps(
            {
                "key_id": str(encrypted.key_id),
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return f"{_ENCRYPTED_SERIALIZATION_PREFIX}{serialization}", envelope

    async def _unprotect_payload(
        self,
        session: AsyncSession,
        scope: RuntimeStateScope,
        *,
        namespace: str,
        checkpoint_id: str,
        suffix: str,
        serialization: str,
        raw: bytes,
    ) -> tuple[str, bytes]:
        if not serialization.startswith(_ENCRYPTED_SERIALIZATION_PREFIX):
            raise RuntimeStateProtocolError(
                "runtime state ciphertext is missing"
            )
        original_serialization = serialization[len(_ENCRYPTED_SERIALIZATION_PREFIX):]
        if original_serialization not in _ALLOWED_SERIALIZATION_TYPES:
            raise RuntimeStateProtocolError(
                "encrypted runtime state has an unsupported serialization"
            )
        try:
            envelope = json.loads(bytes(raw))
            key_id = str(envelope["key_id"])
            ciphertext = str(envelope["ciphertext"])
            nonce = str(envelope["nonce"])
        except Exception as exc:
            raise RuntimeStateProtocolError(
                "encrypted runtime state envelope is invalid"
            ) from exc
        plaintext = await content_encryption_service().decrypt_bytes(
            session,
            key_id=key_id,
            tenant_id=scope.organization_id,
            resource_type="chat",
            resource_id=scope.chat_id,
            purpose="runtime_checkpoint",
            record_id=self._record_id(
                scope,
                namespace=namespace,
                checkpoint_id=checkpoint_id,
                suffix=suffix,
            ),
            ciphertext=ciphertext,
            nonce=nonce,
        )
        return original_serialization, plaintext

    async def put(self, scope: RuntimeStateScope, payload: dict[str, Any]) -> dict:
        namespace = _bounded_text(
            payload.get("checkpoint_ns", ""),
            "checkpoint_ns",
            max_length=512,
            allow_empty=True,
        )
        checkpoint_id = _bounded_text(
            payload.get("checkpoint_id"), "checkpoint_id", max_length=512
        )
        parent_id = _bounded_text(
            payload.get("parent_checkpoint_id", ""),
            "parent_checkpoint_id",
            max_length=512,
            allow_empty=True,
        )
        checkpoint_type, checkpoint_payload = _decode_opaque(
            payload.get("checkpoint"), max_bytes=_MAX_CHECKPOINT_BYTES
        )
        metadata_type, metadata_payload = _decode_opaque(
            payload.get("metadata"), max_bytes=_MAX_CHECKPOINT_BYTES
        )
        metadata_index = _metadata_index(payload.get("metadata_index"))
        async with session_scope(tenant_id=scope.organization_id) as key_session:
            checkpoint_type, checkpoint_payload = await self._protect_payload(
                key_session,
                scope,
                namespace=namespace,
                checkpoint_id=checkpoint_id,
                suffix="checkpoint",
                serialization=checkpoint_type,
                raw=checkpoint_payload,
            )
            metadata_type, metadata_payload = await self._protect_payload(
                key_session,
                scope,
                namespace=namespace,
                checkpoint_id=checkpoint_id,
                suffix="metadata",
                serialization=metadata_type,
                raw=metadata_payload,
            )
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO vc_runtime_checkpoints (
                    organization_id, chat_id, runtime_session_id, thread_id,
                    checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                    checkpoint_serialization, checkpoint_payload,
                    metadata_serialization, metadata_payload, metadata_index
                ) VALUES (%s, %s, %s, %s, %s, %s, NULLIF(%s, ''),
                          %s, %s, %s, %s, %s)
                ON CONFLICT (
                    organization_id, chat_id, runtime_session_id, thread_id,
                    checkpoint_ns, checkpoint_id
                ) DO UPDATE SET
                    parent_checkpoint_id = EXCLUDED.parent_checkpoint_id,
                    checkpoint_serialization = EXCLUDED.checkpoint_serialization,
                    checkpoint_payload = EXCLUDED.checkpoint_payload,
                    metadata_serialization = EXCLUDED.metadata_serialization,
                    metadata_payload = EXCLUDED.metadata_payload,
                    metadata_index = EXCLUDED.metadata_index,
                    updated_at = clock_timestamp()
                """,
                (
                    *self._scope_args(scope),
                    namespace,
                    checkpoint_id,
                    parent_id,
                    checkpoint_type,
                    checkpoint_payload,
                    metadata_type,
                    metadata_payload,
                    Jsonb(metadata_index),
                ),
            )
        return {
            "configurable": {
                "thread_id": scope.thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def put_writes(
        self, scope: RuntimeStateScope, payload: dict[str, Any]
    ) -> None:
        namespace = _bounded_text(
            payload.get("checkpoint_ns", ""),
            "checkpoint_ns",
            max_length=512,
            allow_empty=True,
        )
        checkpoint_id = _bounded_text(
            payload.get("checkpoint_id"), "checkpoint_id", max_length=512
        )
        task_id = _bounded_text(payload.get("task_id"), "task_id", max_length=512)
        task_path = _bounded_text(
            payload.get("task_path", ""),
            "task_path",
            max_length=2048,
            allow_empty=True,
        )
        writes = payload.get("writes")
        if not isinstance(writes, list) or len(writes) > 4096:
            raise RuntimeStateProtocolError("writes must be a bounded list")
        decoded_writes: list[tuple[int, str, str, str, bytes]] = []
        total_bytes = 0
        for item in writes:
            if not isinstance(item, dict):
                raise RuntimeStateProtocolError("write item must be an object")
            index = item.get("index")
            if not isinstance(index, int) or not -1000 <= index <= 1_000_000:
                raise RuntimeStateProtocolError("invalid write index")
            channel = _bounded_text(
                item.get("channel"), "write channel", max_length=512
            )
            value_type, value_payload = _decode_opaque(
                item.get("value"), max_bytes=_MAX_WRITE_BYTES
            )
            total_bytes += len(value_payload)
            if total_bytes > _MAX_CHECKPOINT_BYTES:
                raise RuntimeStateProtocolError("pending writes exceed their limit")
            decoded_writes.append(
                (
                    index,
                    channel,
                    value_type,
                    f"{task_id}|{task_path}|{index}",
                    value_payload,
                )
            )
        if not decoded_writes:
            return
        protected_writes: list[tuple[int, str, str, bytes]] = []
        async with session_scope(tenant_id=scope.organization_id) as key_session:
            for index, channel, value_type, suffix, value_payload in decoded_writes:
                value_type, value_payload = await self._protect_payload(
                    key_session,
                    scope,
                    namespace=namespace,
                    checkpoint_id=checkpoint_id,
                    suffix=f"write|{suffix}|{channel}",
                    serialization=value_type,
                    raw=value_payload,
                )
                protected_writes.append(
                    (index, channel, value_type, value_payload)
                )
        params = [
            (
                *self._scope_args(scope),
                namespace,
                checkpoint_id,
                task_id,
                task_path,
                index,
                channel,
                value_type,
                value_payload,
            )
            for index, channel, value_type, value_payload in protected_writes
        ]
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO vc_runtime_checkpoint_writes (
                        organization_id, chat_id, runtime_session_id, thread_id,
                        checkpoint_ns, checkpoint_id, task_id, task_path,
                        write_index, channel, value_serialization, value_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (
                        organization_id, chat_id, runtime_session_id, thread_id,
                        checkpoint_ns, checkpoint_id, task_id, task_path,
                        write_index
                    ) DO UPDATE SET
                        channel = EXCLUDED.channel,
                        value_serialization = EXCLUDED.value_serialization,
                        value_payload = EXCLUDED.value_payload,
                        updated_at = clock_timestamp()
                    """,
                    params,
                )

    async def _tuple_from_row(
        self,
        scope: RuntimeStateScope,
        row: dict[str, Any],
        *,
        conn: Any,
    ) -> dict[str, Any]:
        namespace = str(row["checkpoint_ns"])
        checkpoint_id = str(row["checkpoint_id"])
        writes = await conn.execute(
            """
            SELECT task_id, task_path, write_index, channel,
                   value_serialization, value_payload
            FROM vc_runtime_checkpoint_writes
            WHERE organization_id = %s AND chat_id = %s
              AND runtime_session_id = %s AND thread_id = %s
              AND checkpoint_ns = %s AND checkpoint_id = %s
            ORDER BY task_id, task_path, write_index
            """,
            (*self._scope_args(scope), namespace, checkpoint_id),
        )
        pending_rows = await writes.fetchall()
        checkpoint_type = str(row["checkpoint_serialization"])
        checkpoint_payload = bytes(row["checkpoint_payload"])
        metadata_type = str(row["metadata_serialization"])
        metadata_payload = bytes(row["metadata_payload"])
        pending: list[dict[str, Any]] = []
        async with session_scope(tenant_id=scope.organization_id) as key_session:
            checkpoint_type, checkpoint_payload = await self._unprotect_payload(
                key_session,
                scope,
                namespace=namespace,
                checkpoint_id=checkpoint_id,
                suffix="checkpoint",
                serialization=checkpoint_type,
                raw=checkpoint_payload,
            )
            metadata_type, metadata_payload = await self._unprotect_payload(
                key_session,
                scope,
                namespace=namespace,
                checkpoint_id=checkpoint_id,
                suffix="metadata",
                serialization=metadata_type,
                raw=metadata_payload,
            )
            for item in pending_rows:
                item_type, item_payload = await self._unprotect_payload(
                    key_session,
                    scope,
                    namespace=namespace,
                    checkpoint_id=checkpoint_id,
                    suffix=(
                        f"write|{item['task_id']}|{item.get('task_path') or ''}|"
                        f"{item['write_index']}|{item['channel']}"
                    ),
                    serialization=str(item["value_serialization"]),
                    raw=bytes(item["value_payload"]),
                )
                pending.append(
                    {
                        "task_id": str(item["task_id"]),
                        "channel": str(item["channel"]),
                        "value": _encode_opaque(item_type, item_payload),
                    }
                )
        config = {
            "configurable": {
                "thread_id": scope.thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }
        parent_id = row.get("parent_checkpoint_id")
        parent = None
        if parent_id:
            parent = {
                "configurable": {
                    "thread_id": scope.thread_id,
                    "checkpoint_ns": namespace,
                    "checkpoint_id": str(parent_id),
                }
            }
        return {
            "config": config,
            "checkpoint": _encode_opaque(checkpoint_type, checkpoint_payload),
            "metadata": _encode_opaque(metadata_type, metadata_payload),
            "parent_config": parent,
            "pending_writes": pending,
        }

    async def get(
        self, scope: RuntimeStateScope, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        namespace = _bounded_text(
            payload.get("checkpoint_ns", ""),
            "checkpoint_ns",
            max_length=512,
            allow_empty=True,
        )
        checkpoint_id = _bounded_text(
            payload.get("checkpoint_id", ""),
            "checkpoint_id",
            max_length=512,
            allow_empty=True,
        )
        pool = await self._get_pool()
        async with pool.connection() as conn:
            if checkpoint_id:
                cursor = await conn.execute(
                    """
                    SELECT * FROM vc_runtime_checkpoints
                    WHERE organization_id = %s AND chat_id = %s
                      AND runtime_session_id = %s AND thread_id = %s
                      AND checkpoint_ns = %s AND checkpoint_id = %s
                    """,
                    (*self._scope_args(scope), namespace, checkpoint_id),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM vc_runtime_checkpoints
                    WHERE organization_id = %s AND chat_id = %s
                      AND runtime_session_id = %s AND thread_id = %s
                      AND checkpoint_ns = %s
                    ORDER BY checkpoint_id DESC LIMIT 1
                    """,
                    (*self._scope_args(scope), namespace),
                )
            row = await cursor.fetchone()
            if row is None:
                return None
            return await self._tuple_from_row(scope, row, conn=conn)

    async def list(
        self, scope: RuntimeStateScope, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        namespace = _bounded_text(
            payload.get("checkpoint_ns", ""),
            "checkpoint_ns",
            max_length=512,
            allow_empty=True,
        )
        before = _bounded_text(
            payload.get("before_checkpoint_id", ""),
            "before_checkpoint_id",
            max_length=512,
            allow_empty=True,
        )
        limit_value = payload.get("limit", 100)
        if not isinstance(limit_value, int) or not 1 <= limit_value <= 1000:
            raise RuntimeStateProtocolError("invalid runtime state list limit")
        filter_value = _metadata_index(payload.get("filter"))
        clauses = [
            "organization_id = %s",
            "chat_id = %s",
            "runtime_session_id = %s",
            "thread_id = %s",
            "checkpoint_ns = %s",
        ]
        params: list[Any] = [*self._scope_args(scope), namespace]
        if before:
            clauses.append("checkpoint_id < %s")
            params.append(before)
        if filter_value:
            clauses.append("metadata_index @> %s")
            params.append(Jsonb(filter_value))
        params.append(limit_value)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM vc_runtime_checkpoints WHERE "
                + " AND ".join(clauses)
                + " ORDER BY checkpoint_id DESC LIMIT %s",
                tuple(params),
            )
            return [
                await self._tuple_from_row(scope, row, conn=conn)
                for row in await cursor.fetchall()
            ]

    async def delete_scope_thread(self, scope: RuntimeStateScope) -> None:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute(
                """
                DELETE FROM vc_runtime_checkpoint_writes
                WHERE organization_id = %s AND chat_id = %s
                  AND runtime_session_id = %s AND thread_id = %s
                """,
                self._scope_args(scope),
            )
            await conn.execute(
                """
                DELETE FROM vc_runtime_checkpoints
                WHERE organization_id = %s AND chat_id = %s
                  AND runtime_session_id = %s AND thread_id = %s
                """,
                self._scope_args(scope),
            )

    async def dispatch(
        self,
        scope: RuntimeStateScope,
        request: dict[str, Any],
    ) -> tuple[str, Any]:
        request_id = str(request.get("request_id") or "")
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise RuntimeStateProtocolError("invalid runtime state request id")
        operation = _bounded_text(
            request.get("operation"), "runtime state operation", max_length=32
        )
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeStateProtocolError("runtime state payload must be an object")
        if operation == "get":
            result = await self.get(scope, payload)
        elif operation == "list":
            result = await self.list(scope, payload)
        elif operation == "put":
            result = await self.put(scope, payload)
        elif operation == "put_writes":
            await self.put_writes(scope, payload)
            result = None
        elif operation == "delete_thread":
            requested_thread = str(payload.get("thread_id") or "")
            if requested_thread != scope.thread_id:
                raise RuntimeStateProtocolError("runtime state scope mismatch")
            await self.delete_scope_thread(scope)
            result = None
        else:
            raise RuntimeStateProtocolError("unsupported runtime state operation")
        return request_id, result

    async def delete(self, thread_id: str) -> bool:
        """Delete a Runtime thread during an already-authorized Chat teardown.

        The compatibility tables are deleted as raw rows; no legacy checkpoint
        payload is deserialized on the host.
        """
        if not thread_id:
            return False
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute(
                "DELETE FROM vc_runtime_checkpoint_writes WHERE thread_id = %s",
                (str(thread_id),),
            )
            await conn.execute(
                "DELETE FROM vc_runtime_checkpoints WHERE thread_id = %s",
                (str(thread_id),),
            )
            existing = await conn.execute(
                "SELECT to_regclass('public.checkpoints') AS checkpoints, "
                "to_regclass('public.checkpoint_blobs') AS blobs, "
                "to_regclass('public.checkpoint_writes') AS writes"
            )
            tables = await existing.fetchone()
            if tables and tables.get("checkpoints"):
                await conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = %s", (str(thread_id),)
                )
            if tables and tables.get("blobs"):
                await conn.execute(
                    "DELETE FROM checkpoint_blobs WHERE thread_id = %s",
                    (str(thread_id),),
                )
            if tables and tables.get("writes"):
                await conn.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id = %s",
                    (str(thread_id),),
                )
        return True


async def runtime_state_response(
    store: LangChainCheckpointStore,
    scope: RuntimeStateScope,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Return a response envelope without exposing SQL/provider error details."""
    request_id = str(request.get("request_id") or "")
    try:
        request_id, result = await store.dispatch(scope, request)
        return {"request_id": request_id, "ok": True, "result": result}
    except RuntimeStateProtocolError as exc:
        return {
            "request_id": request_id,
            "ok": False,
            "error": {"code": "invalid_state_request", "message": str(exc)},
        }
    except Exception:
        logger.exception(
            "runtime_state_broker_failed",
            organization_id=scope.organization_id,
            chat_id=scope.chat_id,
            runtime_session_id=scope.runtime_session_id,
        )
        return {
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "state_unavailable",
                "message": "runtime state service is unavailable",
            },
        }


__all__ = [
    "LangChainCheckpointStore",
    "RuntimeStateProtocolError",
    "RuntimeStateScope",
    "runtime_state_response",
    "setup_runtime_state_schema",
]

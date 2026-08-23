"""One-time plaintext-to-ciphertext migration before strict schema cutover.

This module intentionally uses SQL mappings rather than ORM attributes.  The
latest ORM no longer knows about the legacy plaintext columns, while an
upgrade paused at revision 067 still has them long enough to migrate safely.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import (
    content_encryption_service,
    content_lookup_digest,
)
from vibecanvas_api.security.audit_protection import (
    audit_lookup_digest,
    encrypt_audit_payload,
)
from vibecanvas_api.security.identity_protection import (
    decrypt_user_profile,
    encrypt_account_deletion_email,
    encrypt_provider_uid,
    encrypt_user_profile,
    identity_lookup_digest,
)
from vibecanvas_api.security.vfs_protection import protect_vfs_abstract


_VFS_ABSTRACT_TABLES = {
    "artifact": ("vfs_artifacts", "scope_id"),
    "scratch": ("vfs_scratch", "scope_id"),
    "run": ("vfs_run", "run_id"),
}


async def backfill_vfs_abstracts(
    session: AsyncSession,
    *,
    kind: str,
    resource_id: str,
) -> int:
    """Encrypt legacy non-empty VFS abstracts for one scope or run."""
    try:
        table, resource_column = _VFS_ABSTRACT_TABLES[kind]
    except KeyError as exc:
        raise ValueError("unsupported VFS abstract kind") from exc
    rows = list(
        (
            await session.execute(
                text(
                    f"SELECT tenant_id, path, abstract FROM {table} "
                    f"WHERE {resource_column}=:resource_id AND abstract<>'' "
                    "ORDER BY path FOR UPDATE"
                ),
                {"resource_id": resource_id},
            )
        ).mappings()
    )
    for row in rows:
        values = await protect_vfs_abstract(
            session,
            tenant_id=str(row["tenant_id"]),
            kind=kind,
            resource_id=resource_id,
            path=str(row["path"]),
            abstract=str(row["abstract"]),
        )
        await session.execute(
            text(
                f"UPDATE {table} SET abstract=:abstract, "
                "abstract_ciphertext=:abstract_ciphertext, "
                "abstract_nonce=:abstract_nonce, "
                "abstract_key_id=:abstract_key_id "
                f"WHERE {resource_column}=:resource_id AND path=:path"
            ),
            {**values, "resource_id": resource_id, "path": row["path"]},
        )
    await session.flush()
    return len(rows)


async def backfill_audit_private_payload(
    session: AsyncSession,
    audit_id: str,
) -> int:
    row = (
        await session.execute(
            text(
                "SELECT audit_id, tenant_id, actor_email, target_name, "
                "ip_address, user_agent, meta FROM audit_log "
                "WHERE audit_id=CAST(:audit_id AS uuid) FOR UPDATE"
            ),
            {"audit_id": audit_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return 0
    values = {
        "audit_id": row["audit_id"],
        "actor_hash": audit_lookup_digest("actor_email", row["actor_email"]),
        "ip_hash": audit_lookup_digest("ip_address", row["ip_address"]),
    }
    if row["tenant_id"] is not None:
        encrypted = await encrypt_audit_payload(
            session,
            audit_id=row["audit_id"],
            tenant_id=row["tenant_id"],
            actor_email=row["actor_email"],
            target_name=row["target_name"],
            ip_address=row["ip_address"],
            user_agent=row["user_agent"],
            meta=row["meta"] or {},
        )
        values.update({
            "ciphertext": encrypted.ciphertext,
            "nonce": encrypted.nonce,
            "key_id": encrypted.key_id,
        })
        await session.execute(
            text(
                "UPDATE audit_log SET actor_lookup_hash=:actor_hash, "
                "ip_lookup_hash=:ip_hash, private_ciphertext=:ciphertext, "
                "private_nonce=:nonce, private_key_id=:key_id, "
                "actor_email=NULL, target_name=NULL, ip_address=NULL, "
                "user_agent=NULL, meta='{}'::jsonb "
                "WHERE audit_id=:audit_id"
            ),
            values,
        )
    else:
        await session.execute(
            text(
                "UPDATE audit_log SET actor_lookup_hash=:actor_hash, "
                "ip_lookup_hash=:ip_hash, actor_email=NULL, target_name=NULL, "
                "ip_address=NULL, user_agent=NULL, meta='{}'::jsonb "
                "WHERE audit_id=:audit_id"
            ),
            values,
        )
    await session.flush()
    return 1


async def backfill_account_deletion_emails(
    session: AsyncSession,
    user_id: str,
) -> int:
    user = (
        await session.execute(
            text(
                "SELECT user_id, tenant_id, profile_key_id, profile_ciphertext, "
                "profile_nonce FROM users WHERE user_id=CAST(:user_id AS uuid)"
            ),
            {"user_id": user_id},
        )
    ).mappings().one_or_none()
    if user is None:
        return 0
    profile = await decrypt_user_profile(session, SimpleNamespace(**dict(user)))
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT id FROM account_deletion_requests "
                    "WHERE user_id=CAST(:user_id AS uuid) "
                    "AND email_snapshot_key_id IS NULL ORDER BY requested_at "
                    "FOR UPDATE"
                ),
                {"user_id": user_id},
            )
        ).mappings()
    )
    for row in rows:
        encrypted = await encrypt_account_deletion_email(
            session,
            user_id=user["user_id"],
            tenant_id=user["tenant_id"],
            email=profile.email,
        )
        await session.execute(
            text(
                "UPDATE account_deletion_requests SET "
                "email_snapshot_ciphertext=:ciphertext, "
                "email_snapshot_nonce=:nonce, email_snapshot_key_id=:key_id "
                "WHERE id=:request_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "request_id": row["id"],
            },
        )
    await session.flush()
    return len(rows)


async def backfill_identity_user(session: AsyncSession, user_id: str) -> int:
    """Encrypt one user profile and all provider UIDs at revision 093."""
    user = (
        await session.execute(
            text(
                "SELECT user_id, tenant_id, email, display_name, profile_key_id "
                "FROM users WHERE user_id=CAST(:user_id AS uuid) FOR UPDATE"
            ),
            {"user_id": user_id},
        )
    ).mappings().one_or_none()
    if user is None:
        return 0
    migrated = 0
    if user["profile_key_id"] is None:
        encrypted = await encrypt_user_profile(
            session,
            user_id=user["user_id"],
            tenant_id=user["tenant_id"],
            email=user["email"],
            display_name=user["display_name"] or user["email"],
        )
        await session.execute(
            text(
                "UPDATE users SET profile_ciphertext=:ciphertext, "
                "profile_nonce=:nonce, profile_key_id=:key_id "
                "WHERE user_id=CAST(:user_id AS uuid)"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "user_id": user_id,
            },
        )
        migrated += 1

    identities = list(
        (
            await session.execute(
                text(
                    "SELECT identity_id, provider, provider_uid "
                    "FROM auth_identities WHERE user_id=CAST(:user_id AS uuid) "
                    "AND provider_uid_key_id IS NULL ORDER BY identity_id FOR UPDATE"
                ),
                {"user_id": user_id},
            )
        ).mappings()
    )
    for identity in identities:
        encrypted = await encrypt_provider_uid(
            session,
            identity_id=identity["identity_id"],
            user_id=user["user_id"],
            tenant_id=user["tenant_id"],
            provider=identity["provider"],
            provider_uid=identity["provider_uid"],
        )
        await session.execute(
            text(
                "UPDATE auth_identities SET tenant_id=:tenant_id, "
                "provider_uid_lookup_hash=:lookup_hash, "
                "provider_uid_ciphertext=:ciphertext, "
                "provider_uid_nonce=:nonce, provider_uid_key_id=:key_id "
                "WHERE identity_id=:identity_id"
            ),
            {
                "tenant_id": user["tenant_id"],
                "lookup_hash": identity_lookup_digest(
                    identity["provider"], identity["provider_uid"]
                ),
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "identity_id": identity["identity_id"],
            },
        )
        migrated += 1
    await session.flush()
    return migrated


async def backfill_chat(session: AsyncSession, chat_id: str) -> int:
    chat = (
        await session.execute(
            text(
                "SELECT tenant_id FROM chats WHERE chat_id=:chat_id FOR UPDATE"
            ),
            {"chat_id": chat_id},
        )
    ).mappings().one_or_none()
    if chat is None:
        return 0
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT id, message_id, content, meta FROM chat_messages "
                    "WHERE chat_id=:chat_id AND content_key_id IS NULL "
                    "ORDER BY id FOR UPDATE"
                ),
                {"chat_id": chat_id},
            )
        ).mappings()
    )
    crypto = content_encryption_service()
    for row in rows:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=chat["tenant_id"],
            resource_type="chat",
            resource_id=chat_id,
            purpose="chat_message",
            record_id=str(row["message_id"]),
            value={"content": row["content"], "meta": row["meta"]},
        )
        await session.execute(
            text(
                "UPDATE chat_messages SET content='{}'::jsonb, meta='{}'::jsonb, "
                "content_ciphertext=:ciphertext, content_nonce=:nonce, "
                "content_key_id=:key_id WHERE id=:row_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "row_id": row["id"],
            },
        )
    await session.flush()
    return len(rows)


async def backfill_workflow(session: AsyncSession, wf_id: str) -> int:
    workflow = (
        await session.execute(
            text(
                "SELECT tenant_id FROM workflows WHERE wf_id=:wf_id FOR UPDATE"
            ),
            {"wf_id": wf_id},
        )
    ).mappings().one_or_none()
    if workflow is None:
        return 0
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT major, sub, workflow FROM workflow_versions "
                    "WHERE wf_id=:wf_id AND workflow_key_id IS NULL "
                    "ORDER BY major, sub FOR UPDATE"
                ),
                {"wf_id": wf_id},
            )
        ).mappings()
    )
    crypto = content_encryption_service()
    for row in rows:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=workflow["tenant_id"],
            resource_type="workflow",
            resource_id=wf_id,
            purpose="workflow_version",
            record_id=f"v{row['major']}.sv{row['sub']}",
            value=row["workflow"],
        )
        await session.execute(
            text(
                "UPDATE workflow_versions SET workflow='{}'::jsonb, "
                "workflow_ciphertext=:ciphertext, workflow_nonce=:nonce, "
                "workflow_key_id=:key_id WHERE wf_id=:wf_id "
                "AND major=:major AND sub=:sub"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "wf_id": wf_id,
                "major": row["major"],
                "sub": row["sub"],
            },
        )
    await session.flush()
    return len(rows)


async def backfill_private_display_metadata(
    session: AsyncSession,
    kind: str,
    resource_id: str,
) -> int:
    """Encrypt remaining display metadata while revision 084 is active."""
    crypto = content_encryption_service()
    if kind == "chat":
        row = (
            await session.execute(
                text(
                    "SELECT chat_id, tenant_id, name, meta FROM chats "
                    "WHERE chat_id=:resource_id FOR UPDATE"
                ),
                {"resource_id": resource_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return 0
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=row["tenant_id"],
            resource_type="organization_metadata",
            resource_id=str(row["tenant_id"]),
            purpose="chat_metadata",
            record_id=resource_id,
            value={"name": row["name"] or "", "meta": row["meta"] or {}},
        )
        await session.execute(
            text(
                "UPDATE chats SET name='', meta='{}'::jsonb, "
                "metadata_ciphertext=:ciphertext, metadata_nonce=:nonce, "
                "metadata_key_id=:key_id WHERE chat_id=:resource_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "resource_id": resource_id,
            },
        )
        await session.flush()
        return 1

    if kind == "workflow":
        row = (
            await session.execute(
                text(
                    "SELECT wf_id, tenant_id, workflow_name, description, tags "
                    "FROM workflows WHERE wf_id=:resource_id FOR UPDATE"
                ),
                {"resource_id": resource_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return 0
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=row["tenant_id"],
            resource_type="organization_metadata",
            resource_id=str(row["tenant_id"]),
            purpose="workflow_metadata",
            record_id=resource_id,
            value={
                "workflow_name": row["workflow_name"] or "",
                "description": row["description"] or "",
                "tags": row["tags"] or [],
            },
        )
        await session.execute(
            text(
                "UPDATE workflows SET workflow_name='', description='', "
                "tags='[]'::jsonb, metadata_ciphertext=:ciphertext, "
                "metadata_nonce=:nonce, metadata_key_id=:key_id "
                "WHERE wf_id=:resource_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "resource_id": resource_id,
            },
        )
        versions = list(
            (
                await session.execute(
                    text(
                        "SELECT major, sub, note FROM workflow_versions "
                        "WHERE wf_id=:resource_id AND note_key_id IS NULL "
                        "ORDER BY major, sub FOR UPDATE"
                    ),
                    {"resource_id": resource_id},
                )
            ).mappings()
        )
        for version in versions:
            record_id = f"v{version['major']}.sv{version['sub']}"
            note = await crypto.encrypt_json(
                session,
                tenant_id=row["tenant_id"],
                resource_type="workflow",
                resource_id=resource_id,
                purpose="workflow_version_note",
                record_id=record_id,
                value={"note": version["note"] or ""},
            )
            await session.execute(
                text(
                    "UPDATE workflow_versions SET note='', "
                    "note_ciphertext=:ciphertext, note_nonce=:nonce, "
                    "note_key_id=:key_id WHERE wf_id=:resource_id "
                    "AND major=:major AND sub=:sub"
                ),
                {
                    "ciphertext": note.ciphertext,
                    "nonce": note.nonce,
                    "key_id": note.key_id,
                    "resource_id": resource_id,
                    "major": version["major"],
                    "sub": version["sub"],
                },
            )
        await session.flush()
        return 1 + len(versions)

    if kind == "task_schedule":
        schedule = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, task_id, name, private_ciphertext, "
                    "private_nonce, private_key_id FROM task_schedules "
                    "WHERE id=CAST(:resource_id AS uuid) FOR UPDATE"
                ),
                {"resource_id": resource_id},
            )
        ).mappings().one_or_none()
        if schedule is None:
            return 0
        value = await crypto.decrypt_json(
            session,
            key_id=schedule["private_key_id"],
            tenant_id=schedule["tenant_id"],
            resource_type="task",
            resource_id=str(schedule["task_id"]),
            purpose="task_schedule_private",
            record_id=str(schedule["id"]),
            ciphertext=schedule["private_ciphertext"],
            nonce=schedule["private_nonce"],
        )
        if not isinstance(value, dict):
            raise RuntimeError("Task schedule private payload is invalid")
        value["name"] = schedule["name"] or ""
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=schedule["tenant_id"],
            resource_type="task",
            resource_id=str(schedule["task_id"]),
            purpose="task_schedule_private",
            record_id=str(schedule["id"]),
            value=value,
        )
        await session.execute(
            text(
                "UPDATE task_schedules SET name='', "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id, private_schema_version=2 "
                "WHERE id=CAST(:resource_id AS uuid)"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "resource_id": resource_id,
            },
        )
        await session.flush()
        return 1

    raise ValueError(f"unknown private display metadata kind: {kind}")


async def backfill_task(session: AsyncSession, task_id: str) -> int:
    """Encrypt a Task aggregate while revision 069 still exposes plaintext."""
    task = (
        await session.execute(
            text(
                "SELECT id, tenant_id, payload, result, error FROM tasks "
                "WHERE id=CAST(:task_id AS uuid) FOR UPDATE"
            ),
            {"task_id": task_id},
        )
    ).mappings().one_or_none()
    if task is None:
        return 0
    tenant_id = task["tenant_id"]
    crypto = content_encryption_service()
    migrated = 0

    missing_task = (
        await session.execute(
            text(
                "SELECT content_key_id IS NULL FROM tasks "
                "WHERE id=CAST(:task_id AS uuid)"
            ),
            {"task_id": task_id},
        )
    ).scalar_one()
    if missing_task:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="task",
            resource_id=task_id,
            purpose="task_private",
            record_id=task_id,
            value={
                "payload": task["payload"] or {},
                "result": task["result"],
                "error": task["error"],
            },
        )
        await session.execute(
            text(
                "UPDATE tasks SET payload='{}'::jsonb, result=NULL, error=NULL, "
                "content_ciphertext=:ciphertext, content_nonce=:nonce, "
                "content_key_id=:key_id WHERE id=CAST(:task_id AS uuid)"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "task_id": task_id,
            },
        )
        migrated += 1

    events = list((await session.execute(
        text(
            "SELECT id, payload, encryption_record_id FROM task_events "
            "WHERE task_id=CAST(:task_id AS uuid) AND payload_key_id IS NULL "
            "ORDER BY id FOR UPDATE"
        ),
        {"task_id": task_id},
    )).mappings())
    for event in events:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="task",
            resource_id=task_id,
            purpose="task_event",
            record_id=str(event["encryption_record_id"]),
            value=event["payload"] or {},
        )
        await session.execute(
            text(
                "UPDATE task_events SET payload='{}'::jsonb, "
                "payload_ciphertext=:ciphertext, payload_nonce=:nonce, "
                "payload_key_id=:key_id WHERE id=:event_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "event_id": event["id"],
            },
        )
        migrated += 1

    schedules = list((await session.execute(
        text(
            "SELECT id, input_preset, notification_policy FROM task_schedules "
            "WHERE task_id=CAST(:task_id AS uuid) AND private_key_id IS NULL "
            "FOR UPDATE"
        ),
        {"task_id": task_id},
    )).mappings())
    for schedule in schedules:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="task",
            resource_id=task_id,
            purpose="task_schedule_private",
            record_id=str(schedule["id"]),
            value={
                "input_preset": schedule["input_preset"] or {},
                "notification_policy": schedule["notification_policy"] or {},
            },
        )
        await session.execute(
            text(
                "UPDATE task_schedules SET input_preset='{}'::jsonb, "
                "notification_policy='{}'::jsonb, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE id=:schedule_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "schedule_id": schedule["id"],
            },
        )
        migrated += 1

    executions = list((await session.execute(
        text(
            "SELECT e.id, e.input_snapshot, e.result, e.error, e.run_state, "
            "e.notification_state FROM scheduled_run_executions e "
            "JOIN task_schedules s ON s.id=e.schedule_id "
            "WHERE s.task_id=CAST(:task_id AS uuid) AND e.private_key_id IS NULL "
            "ORDER BY e.triggered_at FOR UPDATE OF e"
        ),
        {"task_id": task_id},
    )).mappings())
    for execution in executions:
        execution_id = str(execution["id"])
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="task_execution",
            resource_id=execution_id,
            purpose="scheduled_execution_private",
            record_id=execution_id,
            value={
                "input_snapshot": execution["input_snapshot"] or {},
                "result": execution["result"],
                "error": execution["error"],
                "run_state": execution["run_state"] or {},
                "notification_state": execution["notification_state"] or {},
            },
        )
        await session.execute(
            text(
                "UPDATE scheduled_run_executions SET "
                "input_snapshot='{}'::jsonb, result=NULL, error=NULL, "
                "run_state='{}'::jsonb, notification_state='{}'::jsonb, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE id=:execution_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "execution_id": execution["id"],
            },
        )
        migrated += 1

    await session.flush()
    return migrated


async def backfill_knowledge_base(session: AsyncSession, kb_id: str) -> int:
    """Encrypt one KB aggregate while revision 071 exposes old columns."""
    kb = (
        await session.execute(
            text(
                "SELECT id, tenant_id, name, description, embedding_dim "
                "FROM knowledge_bases WHERE id=CAST(:kb_id AS uuid) FOR UPDATE"
            ),
            {"kb_id": kb_id},
        )
    ).mappings().one_or_none()
    if kb is None:
        return 0
    tenant_id = kb["tenant_id"]
    crypto = content_encryption_service()
    migrated = 0

    missing_kb = (
        await session.execute(
            text(
                "SELECT private_key_id IS NULL OR name_lookup_hash IS NULL "
                "FROM knowledge_bases WHERE id=CAST(:kb_id AS uuid)"
            ),
            {"kb_id": kb_id},
        )
    ).scalar_one()
    if missing_kb:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="knowledge_base",
            resource_id=kb_id,
            purpose="knowledge_base_private",
            record_id=kb_id,
            value={"name": kb["name"], "description": kb["description"]},
        )
        await session.execute(
            text(
                "UPDATE knowledge_bases SET name='', description=NULL, "
                "name_lookup_hash=:lookup_hash, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE id=CAST(:kb_id AS uuid)"
            ),
            {
                "lookup_hash": content_lookup_digest(
                    tenant_id=tenant_id,
                    namespace="knowledge_base_name",
                    value=kb["name"],
                ),
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "kb_id": kb_id,
            },
        )
        migrated += 1

    files = list(
        (
            await session.execute(
                text(
                    "SELECT id, name, error_message FROM kb_files "
                    "WHERE kb_id=CAST(:kb_id AS uuid) AND private_key_id IS NULL "
                    "ORDER BY id FOR UPDATE"
                ),
                {"kb_id": kb_id},
            )
        ).mappings()
    )
    for file in files:
        file_id = str(file["id"])
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="knowledge_base",
            resource_id=kb_id,
            purpose="knowledge_base_file_private",
            record_id=file_id,
            value={
                "name": file["name"],
                "error_message": file["error_message"],
            },
        )
        await session.execute(
            text(
                "UPDATE kb_files SET name='', error_message=NULL, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE id=:file_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "file_id": file["id"],
            },
        )
        migrated += 1

    chunks = list(
        (
            await session.execute(
                text(
                    "SELECT id, text, chunk_metadata, embedding::text AS embedding "
                    "FROM kb_chunks WHERE kb_id=CAST(:kb_id AS uuid) "
                    "AND content_key_id IS NULL ORDER BY id FOR UPDATE"
                ),
                {"kb_id": kb_id},
            )
        ).mappings()
    )
    for chunk in chunks:
        raw_embedding = chunk["embedding"]
        embedding = (
            list(raw_embedding)
            if isinstance(raw_embedding, (list, tuple))
            else json.loads(str(raw_embedding))
        )
        if not isinstance(embedding, list):
            raise RuntimeError(f"KB chunk {chunk['id']} embedding is invalid")
        chunk_id = str(chunk["id"])
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="knowledge_base",
            resource_id=kb_id,
            purpose="knowledge_base_chunk",
            record_id=chunk_id,
            value={
                "text": chunk["text"],
                "chunk_metadata": chunk["chunk_metadata"] or {},
                "embedding": [float(value) for value in embedding],
            },
        )
        await session.execute(
            text(
                "UPDATE kb_chunks SET text='', chunk_metadata='{}'::jsonb, "
                "embedding=array_fill(0.0::real, ARRAY[:embedding_dim])::vector, "
                "content_ciphertext=:ciphertext, content_nonce=:nonce, "
                "content_key_id=:key_id WHERE id=:chunk_id"
            ),
            {
                "embedding_dim": int(kb["embedding_dim"]),
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "chunk_id": chunk["id"],
            },
        )
        migrated += 1

    await session.flush()
    return migrated


async def backfill_agent_run(session: AsyncSession, run_id: str) -> int:
    """Encrypt one Agent Run and its durable UI replay ledger at revision 076."""
    run = (
        await session.execute(
            text(
                "SELECT run_id, tenant_id, chat_id, input_snapshot, "
                "error_message, private_key_id FROM agent_runs "
                "WHERE run_id=:run_id FOR UPDATE"
            ),
            {"run_id": run_id},
        )
    ).mappings().one_or_none()
    if run is None:
        return 0
    crypto = content_encryption_service()
    migrated = 0
    if run["private_key_id"] is None:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=run["tenant_id"],
            resource_type="chat",
            resource_id=run["chat_id"],
            purpose="agent_run_private",
            record_id=run_id,
            value={
                "input_snapshot": run["input_snapshot"] or {},
                "error_message": run["error_message"],
            },
        )
        await session.execute(
            text(
                "UPDATE agent_runs SET input_snapshot='{}'::jsonb, "
                "error_message=NULL, private_ciphertext=:ciphertext, "
                "private_nonce=:nonce, private_key_id=:key_id "
                "WHERE run_id=:run_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "run_id": run_id,
            },
        )
        migrated += 1

    events = list(
        (
            await session.execute(
                text(
                    "SELECT seq, payload FROM agent_run_events "
                    "WHERE run_id=:run_id AND payload_key_id IS NULL "
                    "ORDER BY seq FOR UPDATE"
                ),
                {"run_id": run_id},
            )
        ).mappings()
    )
    for event in events:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=run["tenant_id"],
            resource_type="chat",
            resource_id=run["chat_id"],
            purpose="agent_run_event",
            record_id=f"{run_id}:{event['seq']}",
            value=event["payload"] or {},
        )
        await session.execute(
            text(
                "UPDATE agent_run_events SET payload='{}'::jsonb, "
                "payload_ciphertext=:ciphertext, payload_nonce=:nonce, "
                "payload_key_id=:key_id WHERE run_id=:run_id AND seq=:seq"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "run_id": run_id,
                "seq": event["seq"],
            },
        )
        migrated += 1
    await session.flush()
    return migrated


async def backfill_hitl_chat(session: AsyncSession, chat_id: str) -> int:
    """Encrypt HITL requests and Interactive Artifacts for one Chat."""
    chat = (
        await session.execute(
            text("SELECT tenant_id FROM chats WHERE chat_id=:chat_id FOR UPDATE"),
            {"chat_id": chat_id},
        )
    ).mappings().one_or_none()
    if chat is None:
        return 0
    tenant_id = chat["tenant_id"]
    crypto = content_encryption_service()
    migrated = 0

    requests = list(
        (
            await session.execute(
                text(
                    "SELECT hitl_request_id, title, prompt_text, ui_payload_json, "
                    "agent_payload_json, decision_payload_json, "
                    "runtime_correlation_json, resume_payload_json, "
                    "interaction_result_json FROM hitl_requests "
                    "WHERE chat_id=:chat_id AND private_key_id IS NULL "
                    "ORDER BY created_at FOR UPDATE"
                ),
                {"chat_id": chat_id},
            )
        ).mappings()
    )
    for row in requests:
        record_id = row["hitl_request_id"]
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="chat",
            resource_id=chat_id,
            purpose="hitl_request_private",
            record_id=record_id,
            value={
                "title": row["title"],
                "prompt_text": row["prompt_text"],
                "ui_payload_json": row["ui_payload_json"] or {},
                "agent_payload_json": row["agent_payload_json"] or {},
                "decision_payload_json": row["decision_payload_json"] or {},
                "runtime_correlation_json": row["runtime_correlation_json"] or {},
                "resume_payload_json": row["resume_payload_json"] or {},
                "interaction_result_json": row["interaction_result_json"] or {},
            },
        )
        await session.execute(
            text(
                "UPDATE hitl_requests SET title='', prompt_text='', "
                "ui_payload_json='{}'::jsonb, agent_payload_json='{}'::jsonb, "
                "decision_payload_json='{}'::jsonb, "
                "runtime_correlation_json='{}'::jsonb, "
                "resume_payload_json='{}'::jsonb, "
                "interaction_result_json='{}'::jsonb, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE hitl_request_id=:record_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "record_id": record_id,
            },
        )
        migrated += 1

    artifacts = list(
        (
            await session.execute(
                text(
                    "SELECT artifact_id, title, definition_json, "
                    "widget_state_json, interaction_result_json, artifact_ref "
                    "FROM interactive_artifacts WHERE chat_id=:chat_id "
                    "AND private_key_id IS NULL ORDER BY created_at FOR UPDATE"
                ),
                {"chat_id": chat_id},
            )
        ).mappings()
    )
    for row in artifacts:
        record_id = row["artifact_id"]
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="chat",
            resource_id=chat_id,
            purpose="interactive_artifact_private",
            record_id=record_id,
            value={
                "title": row["title"],
                "definition_json": row["definition_json"] or {},
                "widget_state_json": row["widget_state_json"] or {},
                "interaction_result_json": row["interaction_result_json"] or {},
                "artifact_ref": row["artifact_ref"],
            },
        )
        await session.execute(
            text(
                "UPDATE interactive_artifacts SET title='', "
                "definition_json='{}'::jsonb, widget_state_json='{}'::jsonb, "
                "interaction_result_json='{}'::jsonb, artifact_ref=NULL, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE artifact_id=:record_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "record_id": record_id,
            },
        )
        migrated += 1
    await session.flush()
    return migrated


async def backfill_background_job(session: AsyncSession, job_id: str) -> int:
    """Encrypt one background job and its durable ordered event ledger."""
    row = (
        await session.execute(
            text(
                "SELECT job_id, tenant_id, chat_id, title, progress_message, "
                "input_snapshot, result_snapshot, result_ref, error_json, "
                "execution_handle_json, private_key_id FROM chat_tool_jobs "
                "WHERE job_id=:job_id FOR UPDATE"
            ),
            {"job_id": job_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return 0
    crypto = content_encryption_service()
    migrated = 0
    if row["private_key_id"] is None:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=row["tenant_id"],
            resource_type="chat",
            resource_id=row["chat_id"],
            purpose="background_job_private",
            record_id=job_id,
            value={
                "title": row["title"],
                "progress_message": row["progress_message"],
                "input_snapshot": row["input_snapshot"] or {},
                "result_snapshot": row["result_snapshot"] or {},
                "result_ref": row["result_ref"],
                "error_json": row["error_json"] or {},
                "execution_handle_json": row["execution_handle_json"] or {},
            },
        )
        await session.execute(
            text(
                "UPDATE chat_tool_jobs SET title='', progress_message='', "
                "input_snapshot='{}'::jsonb, result_snapshot='{}'::jsonb, "
                "result_ref=NULL, error_json='{}'::jsonb, "
                "execution_handle_json='{}'::jsonb, "
                "private_ciphertext=:ciphertext, private_nonce=:nonce, "
                "private_key_id=:key_id WHERE job_id=:job_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "job_id": job_id,
            },
        )
        migrated += 1

    events = list(
        (
            await session.execute(
                text(
                    "SELECT seq, payload FROM chat_tool_job_events "
                    "WHERE job_id=:job_id AND payload_key_id IS NULL "
                    "ORDER BY seq FOR UPDATE"
                ),
                {"job_id": job_id},
            )
        ).mappings()
    )
    for event in events:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=row["tenant_id"],
            resource_type="chat",
            resource_id=row["chat_id"],
            purpose="background_job_event",
            record_id=f"{job_id}:{event['seq']}",
            value=event["payload"] or {},
        )
        await session.execute(
            text(
                "UPDATE chat_tool_job_events SET payload='{}'::jsonb, "
                "payload_ciphertext=:ciphertext, payload_nonce=:nonce, "
                "payload_key_id=:key_id WHERE job_id=:job_id AND seq=:seq"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "job_id": job_id,
                "seq": event["seq"],
            },
        )
        migrated += 1
    await session.flush()
    return migrated


async def backfill_workflow_run(session: AsyncSession, wf_id: str) -> int:
    """Encrypt current Workflow execution state and its ordered event ledger."""
    state = (
        await session.execute(
            text(
                "SELECT wf_id, tenant_id, node_states, error, private_key_id "
                "FROM workflow_run_state WHERE wf_id=:wf_id FOR UPDATE"
            ),
            {"wf_id": wf_id},
        )
    ).mappings().one_or_none()
    if state is None:
        return 0
    crypto = content_encryption_service()
    migrated = 0
    if state["private_key_id"] is None:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=state["tenant_id"],
            resource_type="workflow",
            resource_id=wf_id,
            purpose="workflow_run_state_private",
            record_id=wf_id,
            value={
                "node_states": state["node_states"] or {},
                "error": state["error"],
            },
        )
        await session.execute(
            text(
                "UPDATE workflow_run_state SET node_states='{}'::jsonb, "
                "error=NULL, private_ciphertext=:ciphertext, "
                "private_nonce=:nonce, private_key_id=:key_id WHERE wf_id=:wf_id"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "wf_id": wf_id,
            },
        )
        migrated += 1

    events = list(
        (
            await session.execute(
                text(
                    "SELECT seq, payload FROM workflow_run_events "
                    "WHERE wf_id=:wf_id AND payload_key_id IS NULL "
                    "ORDER BY seq FOR UPDATE"
                ),
                {"wf_id": wf_id},
            )
        ).mappings()
    )
    for event in events:
        encrypted = await crypto.encrypt_json(
            session,
            tenant_id=state["tenant_id"],
            resource_type="workflow",
            resource_id=wf_id,
            purpose="workflow_run_event",
            record_id=f"{wf_id}:{event['seq']}",
            value=event["payload"] or {},
        )
        await session.execute(
            text(
                "UPDATE workflow_run_events SET payload='{}'::jsonb, "
                "payload_ciphertext=:ciphertext, payload_nonce=:nonce, "
                "payload_key_id=:key_id WHERE wf_id=:wf_id AND seq=:seq"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "wf_id": wf_id,
                "seq": event["seq"],
            },
        )
        migrated += 1
    await session.flush()
    return migrated


async def backfill_skill(session: AsyncSession, skill_id: str) -> int:
    """Encrypt immutable revision and mutable draft file bodies for one Skill."""
    skill = (
        await session.execute(
            text(
                "SELECT skill_id, tenant_id FROM skills "
                "WHERE skill_id=:skill_id FOR UPDATE"
            ),
            {"skill_id": skill_id},
        )
    ).mappings().one_or_none()
    if skill is None:
        return 0

    crypto = content_encryption_service()
    migrated = 0
    revisions = list(
        (
            await session.execute(
                text(
                    "SELECT revision_id, path, content "
                    "FROM skill_revision_files WHERE skill_id=:skill_id "
                    "AND content_key_id IS NULL ORDER BY revision_id, path "
                    "FOR UPDATE"
                ),
                {"skill_id": skill_id},
            )
        ).mappings()
    )
    for row in revisions:
        encrypted = await crypto.encrypt_bytes(
            session,
            tenant_id=skill["tenant_id"],
            resource_type="skill",
            resource_id=str(skill["skill_id"]),
            purpose="skill_revision_file",
            record_id=f"{row['revision_id']}:{row['path']}",
            plaintext=bytes(row["content"]),
        )
        await session.execute(
            text(
                "UPDATE skill_revision_files SET content='\\x'::bytea, "
                "content_ciphertext=:ciphertext, content_nonce=:nonce, "
                "content_key_id=:key_id WHERE revision_id=:revision_id "
                "AND path=:path"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "revision_id": row["revision_id"],
                "path": row["path"],
            },
        )
        migrated += 1

    drafts = list(
        (
            await session.execute(
                text(
                    "SELECT path, content FROM skill_draft_files "
                    "WHERE skill_id=:skill_id AND content_key_id IS NULL "
                    "ORDER BY path FOR UPDATE"
                ),
                {"skill_id": skill_id},
            )
        ).mappings()
    )
    for row in drafts:
        encrypted = await crypto.encrypt_bytes(
            session,
            tenant_id=skill["tenant_id"],
            resource_type="skill",
            resource_id=str(skill["skill_id"]),
            purpose="skill_draft_file",
            record_id=f"{skill['skill_id']}:{row['path']}",
            plaintext=bytes(row["content"]),
        )
        await session.execute(
            text(
                "UPDATE skill_draft_files SET content='\\x'::bytea, "
                "content_ciphertext=:ciphertext, content_nonce=:nonce, "
                "content_key_id=:key_id WHERE skill_id=:skill_id AND path=:path"
            ),
            {
                "ciphertext": encrypted.ciphertext,
                "nonce": encrypted.nonce,
                "key_id": encrypted.key_id,
                "skill_id": skill["skill_id"],
                "path": row["path"],
            },
        )
        migrated += 1
    await session.flush()
    return migrated


async def backfill_private_template(
    session: AsyncSession,
    template_id: str,
) -> int:
    """Encrypt one legacy private Template and replace content with sentinels."""
    row = (
        await session.execute(
            text(
                "SELECT template_id, tenant_id, node_type, function_type, "
                "description, agent_hint, display, workflow, tags, "
                "preview_path FROM templates "
                "WHERE template_id=:template_id AND visibility='private' "
                "AND private_key_id IS NULL FOR UPDATE"
            ),
            {"template_id": template_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return 0
    encrypted = await content_encryption_service().encrypt_json(
        session,
        tenant_id=row["tenant_id"],
        resource_type="template",
        resource_id=str(row["template_id"]),
        purpose="template_private_content",
        record_id=str(row["template_id"]),
        value={
            "node_type": row["node_type"],
            "function_type": row["function_type"],
            "description": row["description"],
            "agent_hint": row["agent_hint"] or "",
            "display": row["display"] or {},
            "workflow": row["workflow"] or {},
            "tags": row["tags"] or [],
            "preview_path": row["preview_path"],
        },
    )
    await session.execute(
        text(
            "UPDATE templates SET node_type='', function_type='null'::jsonb, "
            "description='null'::jsonb, "
            "agent_hint='', display='{}'::jsonb, workflow='{}'::jsonb, "
            "tags='[]'::jsonb, preview_path=NULL, "
            "private_ciphertext=:ciphertext, "
            "private_nonce=:nonce, private_key_id=:key_id "
            "WHERE template_id=:template_id"
        ),
        {
            "ciphertext": encrypted.ciphertext,
            "nonce": encrypted.nonce,
            "key_id": encrypted.key_id,
            "template_id": row["template_id"],
        },
    )
    await session.flush()
    return 1

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from vibecanvas_api.security.object_cipher import MAGIC
from vibecanvas_api.services.object_store import FilesystemObjectStore
from vibecanvas_api.services.vfs_volume import (
    EncryptedObjectStoreChatRuntimeVolumeProvider,
    LocalPosixChatRuntimeVolumeProvider,
)


def test_chat_runtime_volume_is_direct_and_durable_across_provider_loss(tmp_path):
    provider = LocalPosixChatRuntimeVolumeProvider(str(tmp_path))
    first = provider.ensure(
        tenant_id="tenant-one",
        user_id="user-one",
        chat_scope_id="chat-one",
    )
    agents = Path(first.path) / ".codex" / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text("keep this guidance", encoding="utf-8")
    database = Path(first.path) / ".codex" / "state.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO state VALUES ('first turn')")
        connection.commit()

    # Recreating the provider models losing the sandbox/session process. There
    # is no hydrate step: the next process receives the exact same directory.
    second = LocalPosixChatRuntimeVolumeProvider(str(tmp_path)).ensure(
        tenant_id="tenant-one",
        user_id="user-one",
        chat_scope_id="chat-one",
    )

    assert second == first
    assert agents.read_text(encoding="utf-8") == "keep this guidance"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM state").fetchone() == (
            "first turn",
        )
    assert os.stat(first.path).st_mode & 0o777 == 0o700


def test_chat_runtime_volume_isolated_and_exact_delete(tmp_path):
    provider = LocalPosixChatRuntimeVolumeProvider(str(tmp_path))
    first = provider.ensure(
        tenant_id="tenant", user_id="user", chat_scope_id="chat-one"
    )
    sibling = provider.ensure(
        tenant_id="tenant", user_id="user", chat_scope_id="chat-two"
    )
    Path(first.path, "marker").write_text("one", encoding="utf-8")
    Path(sibling.path, "marker").write_text("two", encoding="utf-8")

    assert provider.delete(
        tenant_id="tenant", user_id="user", chat_scope_id="chat-one"
    ) is True
    assert not Path(first.path).exists()
    assert Path(sibling.path, "marker").read_text(encoding="utf-8") == "two"
    assert provider.delete(
        tenant_id="tenant", user_id="user", chat_scope_id="chat-one"
    ) is False


def test_chat_runtime_volume_migrates_legacy_codex_directory_once(tmp_path):
    tenant = "tenant"
    user = "user"
    chat_scope = "chat-scope"
    legacy_scope = hashlib.sha256(
        (
            "vibecanvas:codex-state:v2\0"
            f"{tenant}\0{user}\0{chat_scope}"
        ).encode()
    ).hexdigest()
    legacy = tmp_path / tenant / user / "codex-chats-v2" / legacy_scope
    legacy.mkdir(parents=True)
    (legacy / "thread.jsonl").write_text("first reply", encoding="utf-8")

    volume = LocalPosixChatRuntimeVolumeProvider(
        str(tmp_path), legacy_root=str(tmp_path)
    ).ensure(tenant_id=tenant, user_id=user, chat_scope_id=chat_scope)

    assert "chat-runtime-v1" in volume.path
    assert Path(volume.path, "thread.jsonl").read_text(encoding="utf-8") == (
        "first reply"
    )
    assert not legacy.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("tenant_id", "../tenant"), ("user_id", "user/name")],
)
def test_chat_runtime_volume_rejects_unsafe_identity(tmp_path, field, value):
    values = {
        "tenant_id": "tenant",
        "user_id": "user",
        "chat_scope_id": "chat",
    }
    values[field] = value
    provider = LocalPosixChatRuntimeVolumeProvider(str(tmp_path))

    with pytest.raises(ValueError, match=field):
        provider.ensure(**values)


def test_encrypted_runtime_volume_rehydrates_sqlite_and_removes_plaintext(tmp_path):
    cipher_root = tmp_path / "cipher"
    materialized_root = tmp_path / "materialized"
    store = FilesystemObjectStore(
        root=str(cipher_root),
        materialized_root=str(materialized_root),
        master_key=b"K" * 32,
    )
    provider = EncryptedObjectStoreChatRuntimeVolumeProvider(store)
    first = provider.ensure(
        tenant_id="tenant",
        user_id="user",
        chat_scope_id="chat",
    )
    codex = Path(first.path, ".codex")
    codex.mkdir()
    Path(codex, "AGENTS.md").write_text("keep this", encoding="utf-8")
    with sqlite3.connect(Path(codex, "state.sqlite")) as connection:
        connection.execute("CREATE TABLE state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO state VALUES ('resumed')")
        connection.commit()

    assert provider.release(first) >= 2
    assert not Path(first.path).exists()
    keys = store.list_keys(f"{first.storage_prefix}/")
    assert keys
    for key in keys:
        durable = Path(store.root, *key.split("/")).read_bytes()
        assert durable.startswith(MAGIC)
        assert b"keep this" not in durable
        assert b"resumed" not in durable

    second = provider.ensure(
        tenant_id="tenant",
        user_id="user",
        chat_scope_id="chat",
    )
    assert Path(second.path, ".codex", "AGENTS.md").read_text(
        encoding="utf-8"
    ) == "keep this"
    with sqlite3.connect(Path(second.path, ".codex", "state.sqlite")) as connection:
        assert connection.execute("SELECT value FROM state").fetchone() == (
            "resumed",
        )
    provider.release(second)


def test_encrypted_runtime_volume_ignores_symlink_without_reading_target(tmp_path):
    store = FilesystemObjectStore(
        root=str(tmp_path / "cipher"),
        materialized_root=str(tmp_path / "materialized"),
        master_key=b"S" * 32,
    )
    provider = EncryptedObjectStoreChatRuntimeVolumeProvider(store)
    volume = provider.ensure(
        tenant_id="tenant",
        user_id="user",
        chat_scope_id="chat",
    )
    Path(volume.path, "escape").symlink_to(tmp_path / "outside")

    assert provider.sync(volume) == 0
    assert store.list_keys(f"{volume.storage_prefix}/") == []
    store.release_materialized_prefix(volume.storage_prefix or "", volume.path)


def test_encrypted_runtime_volume_tolerates_file_removed_after_manifest(
    monkeypatch,
    tmp_path,
):
    store = FilesystemObjectStore(
        root=str(tmp_path / "cipher"),
        materialized_root=str(tmp_path / "materialized"),
        master_key=b"R" * 32,
    )
    provider = EncryptedObjectStoreChatRuntimeVolumeProvider(store)
    volume = provider.ensure(
        tenant_id="tenant",
        user_id="user",
        chat_scope_id="chat",
    )
    durable = Path(volume.path, "thread.jsonl")
    durable.write_text("keep", encoding="utf-8")
    transient = Path(volume.path, ".codex", "shell_snapshots", "turn.sh")
    transient.parent.mkdir(parents=True)
    transient.write_text("temporary", encoding="utf-8")
    regular_files = provider._regular_files

    def remove_transient_after_manifest(root: str) -> dict[str, str]:
        files = regular_files(root)
        transient.unlink()
        return files

    monkeypatch.setattr(provider, "_regular_files", remove_transient_after_manifest)

    assert provider.sync(volume) == 1
    keys = store.list_keys(f"{volume.storage_prefix}/")
    assert keys == [f"{volume.storage_prefix}/thread.jsonl"]
    store.release_materialized_prefix(volume.storage_prefix or "", volume.path)

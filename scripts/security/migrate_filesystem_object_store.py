#!/usr/bin/env python3
"""Offline, in-place cutover of a local Object Store to VCOBJ2 containers.

Run only while API/workers are stopped.  The application never dual-reads
plaintext objects; deployment must complete this command before starting the
strict runtime.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import tempfile

from vibecanvas_api.config import config
from vibecanvas_api.security.object_cipher import (
    LocalObjectCipher,
    is_encrypted_object_prefix,
)
from vibecanvas_api.security.crypto_core import local_master_key_from_config


def _safe_root(value: str) -> Path:
    root = Path(value).resolve()
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise ValueError("refusing to migrate a broad Object Store root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _write_atomic(cipher: LocalObjectCipher, *, path: Path, key: str, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".vcobj-migrate-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as file:
            fd = -1
            cipher.write(file, key=key, plaintext=data)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def migrate(root: Path, *, check_only: bool) -> tuple[int, int]:
    cipher = LocalObjectCipher(
        local_master_key_from_config(require_persistent=True),
        chunk_size=config.object_store.fs_encryption_chunk_bytes,
    )
    encrypted = 0
    plaintext = 0
    for directory, subdirs, files in os.walk(root, followlinks=False):
        subdirs[:] = [
            name
            for name in subdirs
            if not Path(directory, name).is_symlink()
        ]
        for name in files:
            path = Path(directory, name)
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise RuntimeError("Object Store contains a non-regular entry")
            with path.open("rb") as file:
                prefix = file.read(8)
                if is_encrypted_object_prefix(prefix):
                    encrypted += 1
                    continue
                data = prefix + file.read()
            plaintext += 1
            if not check_only:
                key = path.relative_to(root).as_posix()
                _write_atomic(cipher, path=path, key=key, data=data)
    return encrypted, plaintext


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=config.object_store.fs_root)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = _safe_root(args.root)
    encrypted, plaintext = migrate(root, check_only=args.check)
    if args.check and plaintext:
        print(
            f"filesystem Object Store is not strict: {plaintext} plaintext "
            f"object(s), {encrypted} encrypted object(s)"
        )
        return 2
    print(
        f"filesystem Object Store ciphertext-only: {encrypted + plaintext} "
        f"object(s); transformed={0 if args.check else plaintext}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

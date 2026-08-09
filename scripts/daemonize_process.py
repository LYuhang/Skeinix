#!/usr/bin/env python3
"""Start a local development service as a double-forked daemon."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys


def _write_pid(path: Path, pid: int) -> None:
    temporary = path.with_name(f".{path.name}.{pid}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.write(descriptor, f"{pid}\n".encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def daemonize(*, command: list[str], pid_file: Path, log_file: Path) -> None:
    read_descriptor, write_descriptor = os.pipe()
    first_child = os.fork()
    if first_child:
        os.close(write_descriptor)
        error = b""
        while True:
            chunk = os.read(read_descriptor, 4096)
            if not chunk:
                break
            error += chunk
        os.close(read_descriptor)
        os.waitpid(first_child, 0)
        if error:
            raise RuntimeError(error.decode(errors="replace"))
        return

    try:
        os.close(read_descriptor)
        os.setsid()
        second_child = os.fork()
        if second_child:
            os._exit(0)

        os.umask(0o077)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        log_descriptor = os.open(
            log_file,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        null_descriptor = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null_descriptor, 0)
        os.dup2(log_descriptor, 1)
        os.dup2(log_descriptor, 2)
        if null_descriptor > 2:
            os.close(null_descriptor)
        if log_descriptor > 2:
            os.close(log_descriptor)
        _write_pid(pid_file, os.getpid())
        os.execvpe(command[0], command, os.environ)
    except BaseException as exc:
        try:
            os.write(write_descriptor, str(exc).encode())
        finally:
            os.close(write_descriptor)
        os._exit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid-file", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    try:
        daemonize(
            command=command,
            pid_file=args.pid_file,
            log_file=args.log_file,
        )
    except (OSError, RuntimeError) as exc:
        print(f"daemonize failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

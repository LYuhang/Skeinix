"""Create or validate a mode-0600 development KMS wrapping key file."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import stat
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ensure_local_kms_key.py PATH", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if not path.exists():
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            base64.urlsafe_b64encode(os.urandom(32)).decode("ascii") + "\n",
            encoding="ascii",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    os.chmod(path, 0o600)
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        decoded = base64.urlsafe_b64decode(path.read_text("ascii").strip())
    except Exception as exc:
        raise SystemExit("development KMS key file is not valid base64") from exc
    if mode != 0o600 or len(decoded) != 32:
        raise SystemExit("development KMS key file must be mode 0600 and 32 bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

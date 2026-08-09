"""The native launcher must repair sensitive runtime-file permissions."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "scripts" / "native_dev_up.sh"


def test_native_launcher_repairs_preexisting_runtime_permissions() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'chmod 700 "$RUNDIR"' in source
    assert 'chmod 600 "$RUNDIR/.env.native"' in source
    assert source.index('chmod 600 "$RUNDIR/.env.native"') > source.index(
        'cat > "$RUNDIR/.env.native"'
    )

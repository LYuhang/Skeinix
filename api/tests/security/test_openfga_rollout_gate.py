from __future__ import annotations

import subprocess
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_LIVE_GATE = _ROOT / "scripts/security/verify_openfga_live.sh"
_SECURITY_WORKFLOW = _ROOT / ".github/workflows/security.yml"


def test_openfga_live_gate_exercises_the_complete_model_lifecycle() -> None:
    subprocess.run(["bash", "-n", str(_LIVE_GATE)], check=True)
    gate = _LIVE_GATE.read_text(encoding="utf-8")
    assert gate.count("vibecanvas_api.authorization.model_rollout") == 4
    for command in ("publish", "canary", "promote", "rollback"):
        assert f"  {command}" in gate
    assert '"status"' in gate
    assert '"divergence_count"' in gate
    assert '"latency_ms"' in gate
    assert "HIGHER_CONSISTENCY" in gate
    assert "rolled-back OpenFGA model unexpectedly denied" in gate
    assert "tuple cleanup did not revoke access" in gate


def test_security_workflow_keeps_the_real_openfga_rollout_gate() -> None:
    workflow = _SECURITY_WORKFLOW.read_text(encoding="utf-8")
    assert "authorization-live:" in workflow
    assert "scripts/security/verify_openfga_live.sh" in workflow
    assert "OpenFGA PostgreSQL live contract" in workflow

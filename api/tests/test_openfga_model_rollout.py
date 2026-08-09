from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vibecanvas_api.authorization import bootstrap, model_rollout


SOURCE_SHA = "a" * 40
SOURCE_REF = "refs/tags/v1.2.3"
OLD_MODEL_ID = "model-old"
NEW_MODEL_ID = "model-candidate"


def _local_model_path() -> Path:
    return Path(bootstrap.__file__).with_name("model") / "model.json"


def _local_model() -> dict[str, Any]:
    return json.loads(_local_model_path().read_text(encoding="utf-8"))


OLD_DIGEST = bootstrap.model_sha256(_local_model())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )


def _configure_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENFGA_API_URL", "https://openfga.example.test")
    monkeypatch.setenv("OPENFGA_API_TOKEN", "test-control-plane-token")
    monkeypatch.setenv("OPENFGA_STORE_ID", "store-production")


class FakeOpenFGA:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, Any]] = {
            OLD_MODEL_ID: _local_model(),
        }
        self.checks: dict[tuple[str, str, str, str], bool] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.publish_count = 0

    def request(
        self,
        _api_url: str,
        _token: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/authorization-models"):
            assert body is not None
            self.publish_count += 1
            self.models[NEW_MODEL_ID] = body
            return {"authorization_model_id": NEW_MODEL_ID}
        if method == "GET" and "/authorization-models/" in path:
            model_id = path.rsplit("/", 1)[-1]
            model = self.models.get(model_id)
            if model is None:
                raise RuntimeError("model not found")
            return {"authorization_model": {"id": model_id, **model}}
        if method == "POST" and path.endswith("/check"):
            assert body is not None
            tuple_key = body["tuple_key"]
            key = (
                body["authorization_model_id"],
                tuple_key["user"],
                tuple_key["relation"],
                tuple_key["object"],
            )
            return {"allowed": self.checks[key]}
        raise AssertionError(f"unexpected OpenFGA request: {method} {path}")


def _active_config(path: Path, *, model_id: str = OLD_MODEL_ID) -> None:
    _write_json(
        path,
        {
            "store_id": "store-production",
            "authorization_model_id": model_id,
            "model_sha256": OLD_DIGEST,
            "deployment_owned_metadata": {"keep": True},
        },
    )


def _candidate(path: Path, fake: FakeOpenFGA) -> str:
    model = _local_model()
    digest = bootstrap.model_sha256(model)
    fake.models[NEW_MODEL_ID] = model
    _write_json(
        path,
        {
            "version": 1,
            "store_id": "store-production",
            "authorization_model_id": NEW_MODEL_ID,
            "model_sha256": digest,
            "published_at": "2026-08-01T00:00:00+00:00",
        },
    )
    return digest


def test_publish_is_immutable_idempotent_and_writes_private_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    candidate_path = tmp_path / "candidate.json"

    first = model_rollout.publish(
        model_path=_local_model_path(),
        candidate_path=candidate_path,
    )
    second = model_rollout.publish(
        model_path=_local_model_path(),
        candidate_path=candidate_path,
    )

    assert first == second
    assert first["authorization_model_id"] == NEW_MODEL_ID
    assert first["model_sha256"] == bootstrap.model_sha256(_local_model())
    assert fake.publish_count == 1
    assert candidate_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(candidate_path.read_text(encoding="utf-8")) == first


def test_publish_refuses_to_overwrite_a_different_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    candidate_path = tmp_path / "candidate.json"
    _write_json(
        candidate_path,
        {
            "store_id": "store-production",
            "authorization_model_id": "another-model",
            "model_sha256": "2" * 64,
        },
    )

    with pytest.raises(RuntimeError, match="different model content"):
        model_rollout.publish(
            model_path=_local_model_path(),
            candidate_path=candidate_path,
        )
    assert fake.publish_count == 0


def test_canary_allows_declared_difference_and_promotes_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    config_path = tmp_path / "config.json"
    candidate_path = tmp_path / "candidate.json"
    samples_path = tmp_path / "samples.jsonl"
    evidence_path = tmp_path / "evidence.json"
    _active_config(config_path)
    candidate_digest = _candidate(candidate_path, fake)
    rows = [
        {
            "user": "user:unchanged",
            "relation": "can_view",
            "object": "workflow:one",
        },
        {
            "user": "user:intended-change",
            "relation": "can_view",
            "object": "workflow:two",
            "expected_old": False,
            "expected_new": True,
        },
    ]
    _write_jsonl(samples_path, rows)
    fake.checks.update(
        {
            (OLD_MODEL_ID, "user:unchanged", "can_view", "workflow:one"): True,
            (NEW_MODEL_ID, "user:unchanged", "can_view", "workflow:one"): True,
            (
                OLD_MODEL_ID,
                "user:intended-change",
                "can_view",
                "workflow:two",
            ): False,
            (
                NEW_MODEL_ID,
                "user:intended-change",
                "can_view",
                "workflow:two",
            ): True,
        }
    )

    evidence = model_rollout.canary(
        config_path=config_path,
        candidate_path=candidate_path,
        samples_path=samples_path,
        evidence_path=evidence_path,
        source_sha=SOURCE_SHA,
        source_ref=SOURCE_REF,
    )
    promoted = model_rollout.promote(
        config_path=config_path,
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        model_path=_local_model_path(),
        source_sha=SOURCE_SHA,
        source_ref=SOURCE_REF,
    )

    assert evidence["status"] == "passed"
    assert evidence["sample_count"] == 2
    assert evidence["divergence_count"] == 1
    assert evidence["failure_count"] == 0
    assert set(evidence["latency_ms"]) == {"active", "candidate"}
    for summary in evidence["latency_ms"].values():
        assert set(summary) == {"p50", "p95", "p99", "max"}
        assert all(value >= 0 for value in summary.values())
    assert evidence_path.stat().st_mode & 0o777 == 0o600
    assert promoted["authorization_model_id"] == NEW_MODEL_ID
    assert promoted["model_sha256"] == candidate_digest
    assert promoted["previous_authorization_model_id"] == OLD_MODEL_ID
    assert promoted["model_history"][0]["authorization_model_id"] == OLD_MODEL_ID
    assert promoted["deployment_owned_metadata"] == {"keep": True}
    assert promoted["last_model_promotion"]["source_sha"] == SOURCE_SHA
    assert promoted["last_model_promotion"]["latency_ms"] == evidence["latency_ms"]
    assert json.loads(config_path.read_text(encoding="utf-8")) == promoted
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_unexpected_canary_difference_records_failure_and_blocks_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    config_path = tmp_path / "config.json"
    candidate_path = tmp_path / "candidate.json"
    samples_path = tmp_path / "samples.jsonl"
    evidence_path = tmp_path / "evidence.json"
    _active_config(config_path)
    _candidate(candidate_path, fake)
    row = {
        "user": "user:unexpected-change",
        "relation": "can_update",
        "object": "workflow:one",
    }
    _write_jsonl(samples_path, [row])
    fake.checks.update(
        {
            (OLD_MODEL_ID, *row.values()): False,
            (NEW_MODEL_ID, *row.values()): True,
        }
    )

    with pytest.raises(RuntimeError, match="canary failed for 1 sample"):
        model_rollout.canary(
            config_path=config_path,
            candidate_path=candidate_path,
            samples_path=samples_path,
            evidence_path=evidence_path,
            source_sha=SOURCE_SHA,
            source_ref=SOURCE_REF,
        )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert evidence["divergence_count"] == 1
    with pytest.raises(RuntimeError, match="stale or mismatched: status"):
        model_rollout.promote(
            config_path=config_path,
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            model_path=_local_model_path(),
            source_sha=SOURCE_SHA,
            source_ref=SOURCE_REF,
        )


def test_promotion_rejects_evidence_for_a_stale_active_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    config_path = tmp_path / "config.json"
    candidate_path = tmp_path / "candidate.json"
    samples_path = tmp_path / "samples.jsonl"
    evidence_path = tmp_path / "evidence.json"
    _active_config(config_path)
    _candidate(candidate_path, fake)
    row = {
        "user": "user:stable",
        "relation": "can_view",
        "object": "workflow:one",
    }
    _write_jsonl(samples_path, [row])
    fake.checks.update(
        {
            (OLD_MODEL_ID, *row.values()): True,
            (NEW_MODEL_ID, *row.values()): True,
        }
    )
    model_rollout.canary(
        config_path=config_path,
        candidate_path=candidate_path,
        samples_path=samples_path,
        evidence_path=evidence_path,
        source_sha=SOURCE_SHA,
        source_ref=SOURCE_REF,
    )
    _active_config(config_path, model_id="model-promoted-elsewhere")

    with pytest.raises(
        RuntimeError,
        match="stale or mismatched: active_authorization_model_id",
    ):
        model_rollout.promote(
            config_path=config_path,
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            model_path=_local_model_path(),
            source_sha=SOURCE_SHA,
            source_ref=SOURCE_REF,
        )


def test_promotion_rejects_tampered_latency_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    config_path = tmp_path / "config.json"
    candidate_path = tmp_path / "candidate.json"
    samples_path = tmp_path / "samples.jsonl"
    evidence_path = tmp_path / "evidence.json"
    _active_config(config_path)
    _candidate(candidate_path, fake)
    row = {
        "user": "user:stable",
        "relation": "can_view",
        "object": "workflow:one",
    }
    _write_jsonl(samples_path, [row])
    fake.checks.update(
        {
            (OLD_MODEL_ID, *row.values()): True,
            (NEW_MODEL_ID, *row.values()): True,
        }
    )
    evidence = model_rollout.canary(
        config_path=config_path,
        candidate_path=candidate_path,
        samples_path=samples_path,
        evidence_path=evidence_path,
        source_sha=SOURCE_SHA,
        source_ref=SOURCE_REF,
    )
    evidence["latency_ms"]["candidate"]["p95"] = -1
    _write_json(evidence_path, evidence)

    with pytest.raises(RuntimeError, match="evidence latency is invalid"):
        model_rollout.promote(
            config_path=config_path,
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            model_path=_local_model_path(),
            source_sha=SOURCE_SHA,
            source_ref=SOURCE_REF,
        )


def test_rollback_only_uses_retained_models_and_preserves_current_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    fake.models[NEW_MODEL_ID] = _local_model()
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    config_path = tmp_path / "config.json"
    candidate_digest = bootstrap.model_sha256(_local_model())
    _write_json(
        config_path,
        {
            "store_id": "store-production",
            "authorization_model_id": NEW_MODEL_ID,
            "model_sha256": candidate_digest,
            "previous_authorization_model_id": OLD_MODEL_ID,
            "previous_model_sha256": OLD_DIGEST,
            "model_history": [
                {
                    "authorization_model_id": OLD_MODEL_ID,
                    "model_sha256": OLD_DIGEST,
                    "retained_at": "2026-08-01T00:00:00+00:00",
                }
            ],
        },
    )

    rolled_back = model_rollout.rollback(config_path=config_path, model_id=None)

    assert rolled_back["authorization_model_id"] == OLD_MODEL_ID
    assert rolled_back["model_sha256"] == OLD_DIGEST
    assert rolled_back["previous_authorization_model_id"] == NEW_MODEL_ID
    assert rolled_back["model_history"][0]["authorization_model_id"] == NEW_MODEL_ID
    assert rolled_back["last_model_rollback"] == {
        "from_authorization_model_id": NEW_MODEL_ID,
        "to_authorization_model_id": OLD_MODEL_ID,
        "rolled_back_at": rolled_back["last_model_rollback"]["rolled_back_at"],
    }
    with pytest.raises(RuntimeError, match="not in retained model history"):
        model_rollout.rollback(config_path=config_path, model_id="model-unknown")


def test_rollback_rejects_a_remote_model_that_does_not_match_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_control_plane(monkeypatch)
    fake = FakeOpenFGA()
    altered = _local_model()
    altered["schema_version"] = "1.1"
    fake.models[OLD_MODEL_ID] = altered
    monkeypatch.setattr(bootstrap, "_request_json", fake.request)
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "store_id": "store-production",
            "authorization_model_id": NEW_MODEL_ID,
            "model_sha256": bootstrap.model_sha256(_local_model()),
            "previous_authorization_model_id": OLD_MODEL_ID,
            "previous_model_sha256": OLD_DIGEST,
            "model_history": [
                {
                    "authorization_model_id": OLD_MODEL_ID,
                    "model_sha256": OLD_DIGEST,
                }
            ],
        },
    )

    with pytest.raises(RuntimeError, match="digest does not match history"):
        model_rollout.rollback(config_path=config_path, model_id=None)


@pytest.mark.parametrize(
    ("source_sha", "source_ref", "message"),
    [
        ("abc", SOURCE_REF, "full lowercase Git digest"),
        (SOURCE_SHA, "refs/heads/main", "immutable v\\* tag"),
    ],
)
def test_canary_requires_immutable_release_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_sha: str,
    source_ref: str,
    message: str,
) -> None:
    _configure_control_plane(monkeypatch)
    with pytest.raises(RuntimeError, match=message):
        model_rollout.canary(
            config_path=tmp_path / "config.json",
            candidate_path=tmp_path / "candidate.json",
            samples_path=tmp_path / "samples.jsonl",
            evidence_path=tmp_path / "evidence.json",
            source_sha=source_sha,
            source_ref=source_ref,
        )

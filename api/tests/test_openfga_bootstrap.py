from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vibecanvas_api.authorization import bootstrap


def _local_model() -> dict[str, Any]:
    path = Path(bootstrap.__file__).with_name("model") / "model.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _production_environment(monkeypatch: pytest.MonkeyPatch, output: Path) -> None:
    monkeypatch.setenv("VIBECANVAS_ENV", "production")
    monkeypatch.setenv("OPENFGA_API_URL", "https://openfga.example.test")
    monkeypatch.setenv("OPENFGA_API_TOKEN", "test-control-plane-token")
    monkeypatch.setenv("OPENFGA_STORE_ID", "store-production")
    monkeypatch.setenv("OPENFGA_AUTHORIZATION_MODEL_ID", "model-reviewed")
    monkeypatch.setenv("OPENFGA_BOOTSTRAP_CONFIG_FILE", str(output))
    model = _local_model()
    digest = bootstrap.model_sha256(model)
    monkeypatch.setenv("OPENFGA_MODEL_SHA256", digest)


def test_production_bootstrap_only_reads_and_validates_the_pinned_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "config.json"
    _production_environment(monkeypatch, output)
    calls: list[tuple[str, str]] = []
    local_model = _local_model()

    def request(
        _api_url: str,
        _token: str,
        method: str,
        path: str,
        _body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        calls.append((method, path))
        if path == "/healthz":
            return {"status": "ok"}
        return {"authorization_model": {"id": "model-reviewed", **local_model}}

    monkeypatch.setattr(bootstrap, "_request_json", request)
    bootstrap.main()

    assert calls == [
        ("GET", "/healthz"),
        (
            "GET",
            "/stores/store-production/authorization-models/model-reviewed",
        ),
    ]
    config = json.loads(output.read_text(encoding="utf-8"))
    assert config["store_id"] == "store-production"
    assert config["authorization_model_id"] == "model-reviewed"
    assert output.stat().st_mode & 0o777 == 0o600


def test_production_bootstrap_preserves_rollout_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "config.json"
    _production_environment(monkeypatch, output)
    output.write_text(
        json.dumps(
            {
                "store_id": "store-production",
                "authorization_model_id": "model-previous",
                "model_sha256": "1" * 64,
                "model_history": [
                    {
                        "authorization_model_id": "model-previous",
                        "model_sha256": "1" * 64,
                    }
                ],
                "last_model_promotion": {"source_sha": "a" * 40},
            }
        ),
        encoding="utf-8",
    )
    local_model = _local_model()

    def request(
        _api_url: str,
        _token: str,
        _method: str,
        path: str,
        _body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if path == "/healthz":
            return {"status": "ok"}
        return {"authorization_model": {"id": "model-reviewed", **local_model}}

    monkeypatch.setattr(bootstrap, "_request_json", request)
    bootstrap.main()

    config = json.loads(output.read_text(encoding="utf-8"))
    assert config["model_history"][0]["authorization_model_id"] == "model-previous"
    assert config["last_model_promotion"]["source_sha"] == "a" * 40


def test_production_bootstrap_rejects_remote_model_content_mismatch_without_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "config.json"
    _production_environment(monkeypatch, output)

    def request(
        _api_url: str,
        _token: str,
        method: str,
        path: str,
        _body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if path == "/healthz":
            return {"status": "ok"}
        model = _local_model()
        model["schema_version"] = "unexpected"
        return {"authorization_model": {"id": "model-reviewed", **model}}

    monkeypatch.setattr(bootstrap, "_request_json", request)
    with pytest.raises(RuntimeError, match="content does not match"):
        bootstrap.main()
    assert not output.exists()


def test_remote_model_validation_ignores_only_compiler_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_model = _local_model()
    remote_model = json.loads(json.dumps(local_model))
    for type_definition in remote_model["type_definitions"]:
        metadata = type_definition.get("metadata")
        if not isinstance(metadata, dict):
            continue
        metadata.pop("module", None)
        metadata.pop("source_info", None)
        if not metadata:
            type_definition["metadata"] = None
        elif isinstance(metadata.get("relations"), dict):
            for relation in metadata["relations"].values():
                relation.setdefault("directly_related_user_types", [])
        if "relations" not in type_definition:
            type_definition["relations"] = {}

    monkeypatch.setattr(
        bootstrap,
        "_request_json",
        lambda *_args, **_kwargs: {
            "authorization_model": {"id": "model-reviewed", **remote_model}
        },
    )

    bootstrap.validate_remote_model(
        "https://openfga.example.test",
        "test-token",
        store_id="store-production",
        model_id="model-reviewed",
        expected_model=local_model,
        expected_digest=bootstrap.model_sha256(local_model),
    )


def test_remote_model_validation_rejects_wrong_returned_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_model = _local_model()
    monkeypatch.setattr(
        bootstrap,
        "_request_json",
        lambda *_args, **_kwargs: {
            "authorization_model": {"id": "model-other", **local_model}
        },
    )

    with pytest.raises(RuntimeError, match="different authorization model ID"):
        bootstrap.validate_remote_model(
            "https://openfga.example.test",
            "test-token",
            store_id="store-production",
            model_id="model-reviewed",
            expected_model=local_model,
            expected_digest=bootstrap.model_sha256(local_model),
        )


def test_remote_model_validation_rejects_path_like_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_request_json",
        lambda *_args, **_kwargs: pytest.fail("invalid ID reached the network"),
    )

    with pytest.raises(RuntimeError, match="authorization model ID is invalid"):
        bootstrap.validate_remote_model(
            "https://openfga.example.test",
            "test-token",
            store_id="store-production",
            model_id="../../stores/other",
            expected_model=_local_model(),
            expected_digest=bootstrap.model_sha256(_local_model()),
        )


def test_production_bootstrap_requires_all_explicit_pins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "config.json"
    _production_environment(monkeypatch, output)
    monkeypatch.delenv("OPENFGA_MODEL_SHA256")
    monkeypatch.setattr(
        bootstrap,
        "_request_json",
        lambda *_args, **_kwargs: {"status": "ok"},
    )
    with pytest.raises(
        RuntimeError, match="requires explicit store, model, and digest"
    ):
        bootstrap.main()
    assert not output.exists()

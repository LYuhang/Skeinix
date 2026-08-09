"""Idempotently provision the development OpenFGA store and pinned model."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_CONTROL_PLANE_ID = re.compile(r"[0-9A-Za-z_-]+")


def main() -> None:
    api_url = os.environ.get("OPENFGA_API_URL", "http://openfga:8080").rstrip("/")
    api_token = os.environ.get("OPENFGA_API_TOKEN", "")
    store_name = os.environ.get("OPENFGA_STORE_NAME", "vibecanvas-development")
    output_path = Path(
        os.environ.get(
            "OPENFGA_BOOTSTRAP_CONFIG_FILE",
            "/run/vibecanvas-openfga/config.json",
        )
    )
    model_path = Path(__file__).with_name("model") / "model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model_digest = model_sha256(model)

    _wait_for_health(api_url, api_token)
    existing = _read_existing(output_path)
    if os.environ.get("VIBECANVAS_ENV", "").strip().lower() == "production":
        _validate_production_pin(
            api_url=api_url,
            api_token=api_token,
            output_path=output_path,
            existing=existing,
            local_model=model,
            local_model_digest=model_digest,
        )
        return

    store_id = str(existing.get("store_id") or "")
    model_id = str(existing.get("authorization_model_id") or "")
    if (
        store_id
        and model_id
        and existing.get("model_sha256") == model_digest
        and _model_exists(api_url, api_token, store_id, model_id)
    ):
        _write_config(
            output_path,
            store_id=store_id,
            model_id=model_id,
            model_digest=model_digest,
        )
        return

    if not store_id:
        store_id = _find_store(api_url, api_token, store_name)
    if not store_id:
        payload = _request_json(
            api_url,
            api_token,
            "POST",
            "/stores",
            {"name": store_name},
        )
        store_id = _required_string(payload, "id")

    payload = _request_json(
        api_url,
        api_token,
        "POST",
        f"/stores/{store_id}/authorization-models",
        model,
    )
    model_id = _required_string(payload, "authorization_model_id")
    _write_config(
        output_path,
        store_id=store_id,
        model_id=model_id,
        model_digest=model_digest,
    )


def _validate_production_pin(
    *,
    api_url: str,
    api_token: str,
    output_path: Path,
    existing: dict[str, Any],
    local_model: dict[str, Any],
    local_model_digest: str,
) -> None:
    """Validate an immutable production model without mutating OpenFGA.

    Development bootstrap may create a store/model for convenience. Production
    must instead receive reviewed, explicit IDs from the rollout control plane.
    Reading the remote model back and hashing only the submitted model fields
    prevents a stale or incorrectly labelled model ID from being admitted.
    """

    store_id = str(
        os.environ.get("OPENFGA_STORE_ID") or existing.get("store_id") or ""
    ).strip()
    model_id = str(
        os.environ.get("OPENFGA_AUTHORIZATION_MODEL_ID")
        or existing.get("authorization_model_id")
        or ""
    ).strip()
    configured_digest = str(
        os.environ.get("OPENFGA_MODEL_SHA256") or existing.get("model_sha256") or ""
    ).strip()
    if not store_id or not model_id or not configured_digest:
        raise RuntimeError(
            "production OpenFGA bootstrap requires explicit store, model, and digest pins"
        )
    if configured_digest != local_model_digest:
        raise RuntimeError(
            "production OpenFGA model digest does not match this release"
        )

    validate_remote_model(
        api_url,
        api_token,
        store_id=store_id,
        model_id=model_id,
        expected_model=local_model,
        expected_digest=local_model_digest,
    )

    _write_config(
        output_path,
        store_id=store_id,
        model_id=model_id,
        model_digest=local_model_digest,
    )


def model_sha256(model: dict[str, Any]) -> str:
    """Hash the authorization semantics persisted by OpenFGA.

    The modular-model compiler adds ``metadata.module`` and
    ``metadata.source_info`` fields for source mapping. OpenFGA accepts those
    fields but does not persist them when the JSON model is written through the
    API. They do not affect authorization decisions, so release identity must
    be based on the server-persisted semantic model instead of compiler-local
    source locations.
    """

    return hashlib.sha256(
        json.dumps(
            _canonical_model(model),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _canonical_model(model: dict[str, Any]) -> dict[str, Any]:
    """Remove only non-semantic compiler metadata from an OpenFGA model."""

    canonical = json.loads(json.dumps(model))
    if canonical.get("conditions") == {}:
        canonical.pop("conditions")
    type_definitions = canonical.get("type_definitions")
    if not isinstance(type_definitions, list):
        return canonical
    for type_definition in type_definitions:
        if not isinstance(type_definition, dict):
            continue
        if type_definition.get("relations") == {}:
            type_definition.pop("relations")
        metadata = type_definition.get("metadata")
        if metadata is None:
            type_definition.pop("metadata", None)
            continue
        if not isinstance(metadata, dict):
            continue
        metadata.pop("module", None)
        metadata.pop("source_info", None)
        relation_metadata = metadata.get("relations")
        if isinstance(relation_metadata, dict):
            for relation in relation_metadata.values():
                if not isinstance(relation, dict):
                    continue
                relation.pop("module", None)
                relation.pop("source_info", None)
                directly_related = relation.get("directly_related_user_types")
                if isinstance(directly_related, list):
                    for user_type in directly_related:
                        if not isinstance(user_type, dict):
                            continue
                        if user_type.get("condition") == "":
                            user_type.pop("condition")
                        if user_type.get("relation") == "":
                            user_type.pop("relation")
                if directly_related == []:
                    relation.pop("directly_related_user_types")
        if relation_metadata == {}:
            metadata.pop("relations")
        if not metadata:
            type_definition.pop("metadata", None)
    _remove_openfga_json_defaults(canonical)
    return canonical


def remote_model_sha256(
    api_url: str,
    api_token: str,
    *,
    store_id: str,
    model_id: str,
) -> str:
    """Read an immutable model back and hash only its authorization semantics."""

    _validate_control_plane_id("store", store_id)
    _validate_control_plane_id("authorization model", model_id)

    payload = _request_json(
        api_url,
        api_token,
        "GET",
        f"/stores/{store_id}/authorization-models/{model_id}",
    )
    remote = payload.get("authorization_model", payload)
    if not isinstance(remote, dict):
        raise RuntimeError("OpenFGA returned an invalid authorization model")
    if remote.get("id") != model_id:
        raise RuntimeError("OpenFGA returned a different authorization model ID")
    semantic_fields = {
        key: remote[key]
        for key in ("schema_version", "type_definitions", "conditions")
        if key in remote
    }
    if (
        "schema_version" not in semantic_fields
        or "type_definitions" not in semantic_fields
    ):
        raise RuntimeError("OpenFGA returned an incomplete authorization model")
    return model_sha256(semantic_fields)


def _validate_control_plane_id(kind: str, value: str) -> None:
    if not _CONTROL_PLANE_ID.fullmatch(value):
        raise RuntimeError(f"OpenFGA {kind} ID is invalid")


def _remove_openfga_json_defaults(value: Any) -> None:
    """Normalize optional protobuf string fields returned as empty strings."""

    if isinstance(value, list):
        for item in value:
            _remove_openfga_json_defaults(item)
        return
    if not isinstance(value, dict):
        return
    for key in list(value):
        child = value[key]
        if key in {"condition", "object", "relation"} and child == "":
            value.pop(key)
            continue
        _remove_openfga_json_defaults(child)


def _first_model_difference(expected: Any, actual: Any, path: str = "$") -> str:
    """Return a safe JSON path for rollout diagnostics without dumping policy."""

    if type(expected) is not type(actual):
        return path
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            differing = sorted(set(expected) ^ set(actual))
            return f"{path}.{differing[0]}" if differing else path
        for key in expected:
            difference = _first_model_difference(
                expected[key], actual[key], f"{path}.{key}"
            )
            if difference:
                return difference
        return ""
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}.length"
        for index, item in enumerate(expected):
            difference = _first_model_difference(
                item, actual[index], f"{path}[{index}]"
            )
            if difference:
                return difference
        return ""
    return "" if expected == actual else path


def validate_remote_model(
    api_url: str,
    api_token: str,
    *,
    store_id: str,
    model_id: str,
    expected_model: dict[str, Any],
    expected_digest: str,
) -> None:
    _validate_control_plane_id("store", store_id)
    _validate_control_plane_id("authorization model", model_id)
    payload = _request_json(
        api_url,
        api_token,
        "GET",
        f"/stores/{store_id}/authorization-models/{model_id}",
    )
    remote = payload.get("authorization_model", payload)
    if not isinstance(remote, dict):
        raise RuntimeError("OpenFGA returned an invalid authorization model")
    if remote.get("id") != model_id:
        raise RuntimeError("OpenFGA returned a different authorization model ID")
    # OpenFGA adds server-owned fields such as ``id``. Hash the exact set of
    # semantic fields submitted by this release, and reject missing or altered
    # values. Compiler-only module/source locations are intentionally excluded
    # because OpenFGA does not persist them.
    remote_projection = {key: remote.get(key) for key in expected_model}
    expected_canonical = _canonical_model(expected_model)
    remote_canonical = _canonical_model(remote_projection)
    if remote_canonical != expected_canonical:
        difference = _first_model_difference(expected_canonical, remote_canonical)
        raise RuntimeError(
            f"pinned OpenFGA model content does not match this release at {difference}"
        )
    if model_sha256(remote_projection) != expected_digest:
        raise RuntimeError("pinned OpenFGA model digest verification failed")


def _wait_for_health(api_url: str, token: str) -> None:
    deadline = time.monotonic() + 90
    while True:
        try:
            _request_json(api_url, token, "GET", "/healthz")
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def _model_exists(
    api_url: str,
    token: str,
    store_id: str,
    model_id: str,
) -> bool:
    try:
        _request_json(
            api_url,
            token,
            "GET",
            f"/stores/{store_id}/authorization-models/{model_id}",
        )
    except RuntimeError:
        return False
    return True


def _find_store(api_url: str, token: str, name: str) -> str:
    continuation_token = ""
    while True:
        suffix = (
            f"?continuation_token={continuation_token}" if continuation_token else ""
        )
        payload = _request_json(
            api_url,
            token,
            "GET",
            f"/stores{suffix}",
        )
        stores = payload.get("stores")
        if not isinstance(stores, list):
            raise RuntimeError("OpenFGA returned an invalid store list")
        for store in stores:
            if isinstance(store, dict) and store.get("name") == name:
                return _required_string(store, "id")
        continuation_token = payload.get("continuation_token") or ""
        if not isinstance(continuation_token, str):
            raise RuntimeError("OpenFGA returned an invalid continuation")
        if not continuation_token:
            return ""


def _request_json(
    api_url: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = (
        json.dumps(body, separators=(",", ":")).encode("utf-8")
        if body is not None
        else None
    )
    headers = {"accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = Request(
        f"{api_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("OpenFGA bootstrap request failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OpenFGA bootstrap response was invalid")
    return payload


def _read_existing(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_config(
    path: Path,
    *,
    store_id: str,
    model_id: str,
    model_digest: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        **_read_existing(path),
        "store_id": store_id,
        "authorization_model_id": model_id,
        "model_sha256": model_digest,
    }
    fd, temporary = tempfile.mkstemp(
        prefix=".openfga-",
        dir=str(path.parent),
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"OpenFGA response is missing {key}")
    return value


if __name__ == "__main__":
    main()

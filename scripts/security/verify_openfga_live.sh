#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
work_dir="$(mktemp -d)"
if [[ -n "${POSTGRES_BIN_DIR:-}" ]]; then
  pg_bin="$POSTGRES_BIN_DIR"
elif command -v pg_config >/dev/null 2>&1; then
  pg_bin="$(pg_config --bindir)"
else
  pg_bin="/usr/lib/postgresql/15/bin"
fi
python_bin="${VIBECANVAS_PYTHON:-$repo_root/.venv/bin/python}"
openfga_bin="${OPENFGA_BIN:-$work_dir/openfga}"
api_token="openfga-live-verification-$(openssl rand -hex 16)"
pg_port="${OPENFGA_TEST_PG_PORT:-25491}"
http_port="${OPENFGA_TEST_HTTP_PORT:-28091}"
grpc_port="${OPENFGA_TEST_GRPC_PORT:-28191}"
metrics_port="${OPENFGA_TEST_METRICS_PORT:-22191}"
pg_data="$work_dir/postgres"
bootstrap_file="$work_dir/bootstrap.json"
candidate_file="$work_dir/candidate.json"
canary_samples_file="$work_dir/canary-samples.jsonl"
canary_evidence_file="$work_dir/canary-evidence.json"
openfga_pid=""

cleanup() {
  local status=$?
  if [[ -n "$openfga_pid" ]] && kill -0 "$openfga_pid" 2>/dev/null; then
    kill "$openfga_pid" 2>/dev/null || true
    wait "$openfga_pid" 2>/dev/null || true
  fi
  if [[ -d "$pg_data" ]]; then
    "$pg_bin/pg_ctl" -D "$pg_data" -m fast stop >/dev/null 2>&1 || true
  fi
  rm -r "$work_dir"
  exit "$status"
}
trap cleanup EXIT INT TERM

[[ -x "$python_bin" ]] || {
  printf 'Python environment is not executable: %s\n' "$python_bin" >&2
  exit 2
}
for port in "$pg_port" "$http_port" "$grpc_port" "$metrics_port"; do
  if ss -Hln "sport = :$port" | grep -q .; then
    printf 'OpenFGA verification port is already in use: %s\n' "$port" >&2
    exit 2
  fi
done

if [[ ! -x "$openfga_bin" ]]; then
  "$repo_root/scripts/security/install_openfga_server.sh" "$openfga_bin"
fi

"$pg_bin/initdb" -D "$pg_data" --auth=trust --no-locale >/dev/null
if ! "$pg_bin/pg_ctl" -D "$pg_data" -l "$work_dir/postgres.log" -o \
  "-h 127.0.0.1 -p $pg_port -k $work_dir -F" -w start >/dev/null; then
  cat "$work_dir/postgres.log" >&2
  exit 1
fi
"$pg_bin/createdb" -h 127.0.0.1 -p "$pg_port" openfga

datastore_uri="postgres://$(id -un)@127.0.0.1:$pg_port/openfga?sslmode=disable"
"$openfga_bin" migrate \
  --datastore-engine postgres \
  --datastore-uri "$datastore_uri" \
  --timeout 30s

"$openfga_bin" run \
  --datastore-engine postgres \
  --datastore-uri "$datastore_uri" \
  --authn-method preshared \
  --authn-preshared-keys "$api_token" \
  --http-addr "127.0.0.1:$http_port" \
  --grpc-addr "127.0.0.1:$grpc_port" \
  --metrics-addr "127.0.0.1:$metrics_port" \
  --playground-enabled=false \
  --log-format json \
  >"$work_dir/openfga.log" 2>&1 &
openfga_pid="$!"

export OPENFGA_API_URL="http://127.0.0.1:$http_port"
export OPENFGA_API_TOKEN="$api_token"
export OPENFGA_BOOTSTRAP_CONFIG_FILE="$bootstrap_file"
export OPENFGA_STORE_NAME="vibecanvas-live-verification"
export PYTHONPATH="$repo_root/api/src"
export PYTHONNOUSERSITE=1

"$python_bin" -m vibecanvas_api.authorization.bootstrap

"$python_bin" - "$bootstrap_file" <<'PY'
import asyncio
import json
import os
import statistics
import sys
import time

from vibecanvas_api.authorization.openfga_client import (
    OpenFgaHttpClient,
    OpenFgaTuple,
)
from vibecanvas_api.authorization.types import ConsistencyPreference


async def main() -> None:
    config = json.loads(open(sys.argv[1], encoding="utf-8").read())
    client = OpenFgaHttpClient(
        api_url=os.environ["OPENFGA_API_URL"],
        api_token=os.environ["OPENFGA_API_TOKEN"],
        store_id=config["store_id"],
        authorization_model_id=config["authorization_model_id"],
        timeout_seconds=5,
    )
    tuples = (
        OpenFgaTuple(
            "user:live-user",
            "owner",
            "organization:live-organization",
        ),
        OpenFgaTuple(
            "organization:live-organization",
            "organization",
            "workflow:live-workflow",
        ),
        OpenFgaTuple(
            "user:live-user",
            "manager",
            "workflow:live-workflow",
        ),
    )
    try:
        await client.probe()
        await client.write(writes=tuples)
        allowed = await client.check(
            user="user:live-user",
            relation="can_update",
            object_="workflow:live-workflow",
            consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
        )
        if not allowed:
            raise RuntimeError("live OpenFGA check unexpectedly denied")
        batch = await client.batch_check(
            (
                (
                    "user:live-user",
                    "can_update",
                    "workflow:live-workflow",
                ),
                (
                    "user:unknown",
                    "can_update",
                    "workflow:live-workflow",
                ),
            ),
            consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
        )
        if batch != (True, False):
            raise RuntimeError(f"unexpected BatchCheck result: {batch!r}")
        listed = await client.list_objects(
            user="user:live-user",
            relation="can_view",
            object_type="workflow",
            consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
        )
        if listed != ("live-workflow",):
            raise RuntimeError(f"unexpected ListObjects result: {listed!r}")

        # Keep a deployment-machine baseline for the two permission paths that
        # sit on list-page and capability rendering hot paths.  This measures
        # the real HTTP client, pinned model, OpenFGA server, and PostgreSQL;
        # it does not introduce a shared allow cache that could outlive revoke.
        samples = max(10, int(os.environ.get("OPENFGA_BENCHMARK_SAMPLES", "100")))
        batch_checks = tuple(
            (
                "user:live-user" if index % 2 == 0 else "user:unknown",
                "can_update",
                "workflow:live-workflow",
            )
            for index in range(50)
        )
        list_latencies: list[float] = []
        batch_latencies: list[float] = []
        for _ in range(samples):
            started = time.perf_counter()
            await client.list_objects(
                user="user:live-user",
                relation="can_view",
                object_type="workflow",
                consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
            )
            list_latencies.append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            await client.batch_check(
                batch_checks,
                consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
            )
            batch_latencies.append((time.perf_counter() - started) * 1000)

        def percentile(values: list[float], fraction: float) -> float:
            ordered = sorted(values)
            index = min(len(ordered) - 1, int(len(ordered) * fraction))
            return ordered[index]

        for label, values in (
            ("openfga_list_objects", list_latencies),
            ("openfga_batch_check_50", batch_latencies),
        ):
            print(f"{label}_samples={len(values)}")
            print(f"{label}_p50_ms={statistics.median(values):.3f}")
            print(f"{label}_p95_ms={percentile(values, 0.95):.3f}")
            print(f"{label}_p99_ms={percentile(values, 0.99):.3f}")
    finally:
        await client.close()


asyncio.run(main())
PY

export OPENFGA_STORE_ID="$($python_bin - "$bootstrap_file" <<'PY'
import json
import sys

print(json.loads(open(sys.argv[1], encoding="utf-8").read())["store_id"])
PY
)"
rollout_source_sha="$(git -C "$repo_root" rev-parse HEAD)"
rollout_source_ref="refs/tags/v0.0.0-openfga-live-contract"
model_file="$repo_root/api/src/vibecanvas_api/authorization/model/model.json"

cat >"$canary_samples_file" <<'JSONL'
{"user":"user:live-user","relation":"can_update","object":"workflow:live-workflow"}
{"user":"user:unknown","relation":"can_update","object":"workflow:live-workflow"}
{"user":"user:live-user","relation":"can_view","object":"workflow:live-workflow"}
JSONL

"$python_bin" -m vibecanvas_api.authorization.model_rollout \
  --config-file "$bootstrap_file" \
  publish \
  --model "$model_file" \
  --candidate-file "$candidate_file"
"$python_bin" -m vibecanvas_api.authorization.model_rollout \
  --config-file "$bootstrap_file" \
  canary \
  --candidate-file "$candidate_file" \
  --samples "$canary_samples_file" \
  --evidence-file "$canary_evidence_file" \
  --source-sha "$rollout_source_sha" \
  --source-ref "$rollout_source_ref"
"$python_bin" -m vibecanvas_api.authorization.model_rollout \
  --config-file "$bootstrap_file" \
  promote \
  --candidate-file "$candidate_file" \
  --evidence-file "$canary_evidence_file" \
  --model "$model_file" \
  --source-sha "$rollout_source_sha" \
  --source-ref "$rollout_source_ref"
"$python_bin" -m vibecanvas_api.authorization.model_rollout \
  --config-file "$bootstrap_file" \
  rollback

"$python_bin" - "$bootstrap_file" "$candidate_file" "$canary_evidence_file" <<'PY'
import asyncio
import json
import os
from pathlib import Path
import stat
import sys

from vibecanvas_api.authorization.openfga_client import (
    OpenFgaHttpClient,
    OpenFgaTuple,
)
from vibecanvas_api.authorization.types import ConsistencyPreference


def private_json(path: str) -> dict[str, object]:
    artifact = Path(path)
    if stat.S_IMODE(artifact.stat().st_mode) != 0o600:
        raise RuntimeError(f"rollout artifact is not private: {artifact}")
    return json.loads(artifact.read_text(encoding="utf-8"))


async def main() -> None:
    config = private_json(sys.argv[1])
    candidate = private_json(sys.argv[2])
    evidence = private_json(sys.argv[3])
    rollback = config.get("last_model_rollback")
    if not isinstance(rollback, dict):
        raise RuntimeError("live rollout did not record rollback evidence")
    if config["authorization_model_id"] != rollback["to_authorization_model_id"]:
        raise RuntimeError("live rollback did not restore the original model")
    if candidate["authorization_model_id"] != rollback["from_authorization_model_id"]:
        raise RuntimeError("live rollback did not retain the promoted model")
    if evidence.get("status") != "passed" or evidence.get("sample_count") != 3:
        raise RuntimeError("live model canary evidence is incomplete")
    if evidence.get("divergence_count") != 0:
        raise RuntimeError("identical live model canary unexpectedly diverged")
    latency = evidence.get("latency_ms")
    if not isinstance(latency, dict):
        raise RuntimeError("live model canary did not retain latency evidence")
    for model_name in ("active", "candidate"):
        summary = latency.get(model_name)
        if not isinstance(summary, dict) or not all(
            isinstance(summary.get(key), (int, float))
            and summary[key] >= 0
            for key in ("p50", "p95", "p99", "max")
        ):
            raise RuntimeError(f"invalid {model_name} canary latency evidence")

    client = OpenFgaHttpClient(
        api_url=os.environ["OPENFGA_API_URL"],
        api_token=os.environ["OPENFGA_API_TOKEN"],
        store_id=str(config["store_id"]),
        authorization_model_id=str(config["authorization_model_id"]),
        timeout_seconds=5,
    )
    tuples = (
        OpenFgaTuple(
            "user:live-user",
            "owner",
            "organization:live-organization",
        ),
        OpenFgaTuple(
            "organization:live-organization",
            "organization",
            "workflow:live-workflow",
        ),
        OpenFgaTuple(
            "user:live-user",
            "manager",
            "workflow:live-workflow",
        ),
    )
    try:
        allowed = await client.check(
            user="user:live-user",
            relation="can_update",
            object_="workflow:live-workflow",
            consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
        )
        if not allowed:
            raise RuntimeError("rolled-back OpenFGA model unexpectedly denied")
        await client.write(deletes=tuples)
        allowed_after_delete = await client.check(
            user="user:live-user",
            relation="can_update",
            object_="workflow:live-workflow",
            consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
        )
        if allowed_after_delete:
            raise RuntimeError("live OpenFGA tuple cleanup did not revoke access")
    finally:
        await client.close()


asyncio.run(main())
PY

curl -fsS -H "Authorization: Bearer $api_token" \
  "http://127.0.0.1:$metrics_port/metrics" \
  -o "$work_dir/metrics.txt"
grep -q "openfga" "$work_dir/metrics.txt"
printf 'OpenFGA live PostgreSQL and model rollout verification passed.\n'

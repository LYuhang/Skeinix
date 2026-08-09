#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
ENV_FILE="${VIBECANVAS_ENV_FILE:-$REPO_ROOT/.env}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

require_nonplaceholder() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  [[ -n "$value" ]] || fail "$key is missing from $ENV_FILE"
  case "$value" in
    dev|vc_app|vc_migrator|vc_maintenance|sk-...|*development-only*|*change-me*)
      fail "$key still uses a placeholder/default value in $ENV_FILE"
      ;;
  esac
}

require_command docker
require_command curl
require_command openssl
require_command awk

docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin is unavailable"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable to the current user"

[[ -f "$ENV_FILE" ]] || fail "$ENV_FILE does not exist; run scripts/deploy/local_server.sh init"
chmod 600 "$ENV_FILE"

require_nonplaceholder POSTGRES_PASSWORD
require_nonplaceholder VIBECANVAS_APP_PASSWORD
require_nonplaceholder VIBECANVAS_MIGRATOR_PASSWORD
require_nonplaceholder VIBECANVAS_MAINTENANCE_PASSWORD
require_nonplaceholder OPENFGA_POSTGRES_PASSWORD
require_nonplaceholder OPENFGA_API_TOKEN
require_nonplaceholder KMS_LOCAL_MASTER_KEY
require_nonplaceholder CONTENT_LOOKUP_HMAC_KEY
require_nonplaceholder BROWSER_TOKEN_SECRET
require_nonplaceholder VIBECANVAS_SIGNING_SECRET

if [[ "$(env_value KMS_PROVIDER)" != "local" ]]; then
  fail "the local-server entrypoint expects KMS_PROVIDER=local"
fi

bind_address="$(env_value VIBECANVAS_BIND_ADDRESS)"
if [[ -z "$bind_address" ]]; then
  fail "VIBECANVAS_BIND_ADDRESS must be explicit"
fi
if [[ "$bind_address" == "0.0.0.0" || "$bind_address" == "::" ]]; then
  fail "wildcard network binding is not allowed by the local-server entrypoint"
fi

if [[ -r /proc/sys/kernel/unprivileged_userns_clone ]] &&
   [[ "$(< /proc/sys/kernel/unprivileged_userns_clone)" == "0" ]]; then
  fail "kernel.unprivileged_userns_clone=0; workflow gVisor sandboxes cannot start"
fi

VIBECANVAS_ENV_FILE="$ENV_FILE" \
  docker compose --env-file "$ENV_FILE" config --quiet

echo "preflight=pass"
echo "env_file=$ENV_FILE"
echo "bind_address=$bind_address"

#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
ENV_FILE="${VIBECANVAS_ENV_FILE:-$REPO_ROOT/.env}"
PREFLIGHT="$REPO_ROOT/scripts/deploy/preflight.sh"
VERIFY="$REPO_ROOT/scripts/deploy/verify_local.sh"

random_hex() {
  openssl rand -hex "${1:-32}"
}

random_urlsafe_key() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
}

set_env_value() {
  local key="$1"
  local value="$2"
  local temporary
  temporary="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      if (!replaced) print key "=" value
      replaced = 1
      next
    }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$ENV_FILE" >"$temporary"
  chmod 600 "$temporary"
  mv "$temporary" "$ENV_FILE"
}

initialize_env() {
  command -v openssl >/dev/null 2>&1 || {
    echo "ERROR: openssl is required to generate local secrets" >&2
    exit 1
  }
  if [[ -e "$ENV_FILE" ]]; then
    chmod 600 "$ENV_FILE"
    echo "existing env preserved without modification: $ENV_FILE"
    return
  fi

  install -m 600 "$REPO_ROOT/.env.example" "$ENV_FILE"
  set_env_value VIBECANVAS_BIND_ADDRESS "${VIBECANVAS_BIND_ADDRESS:-127.0.0.1}"
  set_env_value VIBECANVAS_HTTP_PORT "${VIBECANVAS_HTTP_PORT:-9001}"
  set_env_value VIBECANVAS_PUBLIC_URL "${VIBECANVAS_PUBLIC_URL:-http://localhost:${VIBECANVAS_HTTP_PORT:-9001}}"
  set_env_value POSTGRES_PASSWORD "$(random_hex 24)"
  set_env_value VIBECANVAS_APP_PASSWORD "$(random_hex 24)"
  set_env_value VIBECANVAS_MIGRATOR_PASSWORD "$(random_hex 24)"
  set_env_value VIBECANVAS_MAINTENANCE_PASSWORD "$(random_hex 24)"
  set_env_value OPENFGA_POSTGRES_PASSWORD "$(random_hex 24)"
  set_env_value OPENFGA_API_TOKEN "$(random_hex 32)"
  set_env_value KMS_PROVIDER local
  set_env_value KMS_KEY_ID vibecanvas-local-development
  set_env_value KMS_LOCAL_MASTER_KEY "$(random_urlsafe_key)"
  set_env_value CONTENT_LOOKUP_HMAC_KEY "$(random_urlsafe_key)"
  set_env_value BROWSER_TOKEN_SECRET "$(random_urlsafe_key)"
  set_env_value VIBECANVAS_SIGNING_SECRET "$(random_urlsafe_key)"
  set_env_value OAUTH_ENCRYPTION_KEY "$(random_urlsafe_key)"
  set_env_value ENABLE_TEST_USER false
  set_env_value ENTERPRISE_SSO_ENABLED false
  set_env_value AGENT_RUNTIME_TYPES langchain,codex
  set_env_value CODEX_RUNTIME_AUTH_METHODS chatgpt,managed_api,personal_api
  set_env_value CODEX_MANAGED_APIS_JSON '[]'
  set_env_value AGENT_DEBUG_VIEW_ENABLED false
  set_env_value BROWSER_DEBUG_SEND false
  set_env_value SANDBOX_TYPE rootful-snapshot
  set_env_value SANDBOX_EGRESS_MODE "${SANDBOX_EGRESS_MODE:-proxy}"
  set_env_value SANDBOX_AGENT_EGRESS_POLICY "${SANDBOX_AGENT_EGRESS_POLICY:-public}"

  echo "created $ENV_FILE with mode 0600"
  echo "optional: edit model credentials in $ENV_FILE before first AI request"
}

compose() {
  VIBECANVAS_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" "$@"
}

configure_synthetic_dns() {
  local egress_mode trusted_cidrs suggestion
  egress_mode="$(sed -n 's/^SANDBOX_EGRESS_MODE=//p' "$ENV_FILE" | tail -n 1)"
  trusted_cidrs="$(sed -n 's/^SANDBOX_EGRESS_TRUSTED_PROXY_CIDRS=//p' "$ENV_FILE" | tail -n 1)"
  if [[ "$egress_mode" != "proxy" || -n "$trusted_cidrs" ]]; then
    return
  fi
  suggestion="$(compose exec -T sandboxd \
    python -m vibecanvas_api.services.sandbox.network_diagnostics \
    --suggest-cidr 2>/dev/null || true)"
  if [[ -z "$suggestion" ]]; then
    return
  fi
  echo "detected reachable VPN/transparent-proxy fake-IP range: $suggestion"
  set_env_value SANDBOX_EGRESS_TRUSTED_PROXY_CIDRS "$suggestion"
  echo "recreating sandboxd with the explicit trusted synthetic DNS range"
  compose up -d --force-recreate --wait sandboxd
}

start_stack() {
  initialize_env
  VIBECANVAS_ENV_FILE="$ENV_FILE" bash "$PREFLIGHT"
  compose up -d --build --wait
  configure_synthetic_dns
  VIBECANVAS_ENV_FILE="$ENV_FILE" bash "$VERIFY"
}

case "${1:-up}" in
  init)
    initialize_env
    ;;
  preflight)
    VIBECANVAS_ENV_FILE="$ENV_FILE" bash "$PREFLIGHT"
    ;;
  up|start)
    start_stack
    ;;
  restart)
    initialize_env
    compose down
    start_stack
    ;;
  verify)
    VIBECANVAS_ENV_FILE="$ENV_FILE" bash "$VERIFY"
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs -f "${@:2}"
    ;;
  stop|down)
    compose down
    ;;
  config)
    compose config
    ;;
  *)
    echo "usage: $0 {init|preflight|up|restart|verify|status|logs [service]|stop|config}" >&2
    exit 2
    ;;
esac

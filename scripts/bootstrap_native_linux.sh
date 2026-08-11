#!/usr/bin/env bash
set -euo pipefail
umask 077

# Bootstrap a fresh Debian/Ubuntu/WSL host for the native (non-Docker) stack.
# The script is intentionally idempotent. It installs host packages, creates a
# repo-local uv Python environment, installs pinned project dependencies,
# prepares Node/pnpm, fetches the pinned gVisor binary, and starts the app.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PREPARE_ONLY=0
UV_VERSION="${UV_VERSION:-0.11.32}"
NODE_MAJOR="${NODE_MAJOR:-22}"
CODEX_CLI_VERSION="${CODEX_CLI_VERSION:-0.147.0}"

usage() {
  cat <<'EOF'
usage: ./scripts/bootstrap_native_linux.sh [--prepare-only]

  --prepare-only  install and configure dependencies without starting services
EOF
}

for argument in "$@"; do
  case "$argument" in
    --prepare-only) PREPARE_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || {
  echo "ERROR: this bootstrap supports Linux and WSL only" >&2
  exit 1
}
command -v apt-get >/dev/null || {
  echo "ERROR: apt-get is required; see docs/installation.md for manual installation" >&2
  exit 1
}
[[ "${EUID}" -ne 0 ]] || {
  echo "ERROR: run this script as your normal login user, not root; it will use sudo for apt" >&2
  exit 1
}
command -v sudo >/dev/null || {
  echo "ERROR: sudo is required for host package installation" >&2
  exit 1
}

echo "[1/7] Installing host packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential ca-certificates curl file git gnupg jq libpq-dev openssh-client \
  openssl patch postgresql postgresql-contrib procps redis-server ripgrep rsync \
  tar unzip util-linux zip \
  fonts-dejavu-core fonts-noto-cjk fonts-wqy-zenhei

node_is_compatible() {
  command -v node >/dev/null || return 1
  node -e '
    const [major, minor] = process.versions.node.split(".").map(Number);
    process.exit(major > 22 || major === 22 || (major === 20 && minor >= 19) ? 0 : 1);
  '
}

if ! node_is_compatible; then
  echo "[2/7] Installing Node.js ${NODE_MAJOR}.x"
  node_setup="$(mktemp)"
  curl -fsSL --retry 3 "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" -o "$node_setup"
  sudo -E bash "$node_setup"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
else
  echo "[2/7] Reusing compatible Node.js $(node --version)"
fi

echo "[3/7] Installing the project-pinned pnpm and Codex CLI"
if command -v corepack >/dev/null; then
  sudo corepack enable
  corepack prepare pnpm@10.34.4 --activate
else
  sudo npm install --global pnpm@10.34.4
fi
if [[ "$(codex --version 2>/dev/null || true)" != "codex-cli ${CODEX_CLI_VERSION}" ]]; then
  sudo npm install --global --ignore-scripts --no-audit --no-fund \
    "@openai/codex@${CODEX_CLI_VERSION}"
fi
[[ "$(codex --version)" == "codex-cli ${CODEX_CLI_VERSION}" ]] || {
  echo "ERROR: expected codex-cli ${CODEX_CLI_VERSION}, got $(codex --version 2>/dev/null || echo missing)" >&2
  exit 1
}

if ! command -v uv >/dev/null; then
  echo "[4/7] Installing uv ${UV_VERSION}"
  uv_installer="$(mktemp)"
  curl -LsSf --retry 3 "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$uv_installer"
  UV_NO_MODIFY_PATH=1 sh "$uv_installer"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
else
  echo "[4/7] Reusing uv $(uv --version)"
fi
command -v uv >/dev/null || {
  echo "ERROR: uv was installed but is not on PATH; add ~/.local/bin and retry" >&2
  exit 1
}

echo "[5/7] Creating the repo-local Python 3.11.15 environment"
cd "$REPO_ROOT"
uv python install 3.11.15
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.11.15 --seed .venv
fi
uv pip install --python .venv/bin/python --require-hashes \
  --requirement requirements-build.txt
uv pip install --python .venv/bin/python --requirement requirements-dev.txt
uv pip install --python .venv/bin/python --require-hashes \
  --requirement requirements-sandbox.txt
uv pip install --python .venv/bin/python --no-build-isolation --no-deps \
  --editable ./engine --editable ./api
.venv/bin/python -c \
  'import fastapi, jsonlines, langgraph, matplotlib, networkx, numpy, pandas, psycopg, seaborn, sqlalchemy, tabulate, vibecanvas_api, vibecanvas_engine; print("Python environment: ok")'

echo "[6/7] Installing Web and extension packages"
pnpm --dir web install --frozen-lockfile
pnpm --dir extension install --frozen-lockfile

echo "[7/7] Preparing the pinned gVisor runtime and local config"
# Populate the verified per-user cache. The launcher resolves this path on each
# start, so moving the checkout cannot leave a stale repository path behind.
bash scripts/get_runsc.sh >/dev/null
launch_env="$REPO_ROOT/.env.launch.local"
if [[ ! -e "$launch_env" ]]; then
  cat >"$launch_env" <<EOF
# Local native deployment. This file is mode 0600 and is never committed.
WEB_HOST="::"
WEB_PORT=9001
VIBECANVAS_PUBLIC_URL="http://localhost:9001/"
WEB_ALLOWED_HOSTS="localhost,127.0.0.1,::1"
VIBECANVAS_API_CORS_ORIGINS="http://localhost:9001,http://127.0.0.1:9001,http://[::1]:9001"
ENABLE_TEST_USER=false
ENTERPRISE_SSO_ENABLED=false
AGENT_RUNTIME_TYPES="langchain,codex"
CODEX_RUNTIME_AUTH_METHODS="chatgpt,managed_api,personal_api"
CODEX_MANAGED_APIS_JSON='[]'
EOF
  chmod 600 "$launch_env"
  echo "Created $launch_env"
else
  echo "Kept existing $launch_env unchanged"
fi

if [[ "$PREPARE_ONLY" == "1" ]]; then
  echo "Preparation complete. Start later with: ./launch.sh start"
  exit 0
fi

exec "$REPO_ROOT/launch.sh" start

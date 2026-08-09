#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

scan_history=1
if [[ "${1:-}" == "--working-tree-only" ]]; then
  scan_history=0
  shift
fi
if (( $# != 0 )); then
  printf 'Usage: %s [--working-tree-only]\n' "$0" >&2
  exit 2
fi

gitleaks_bin="${GITLEAKS_BIN:-gitleaks}"
if ! command -v "$gitleaks_bin" >/dev/null 2>&1 && [[ ! -x "$gitleaks_bin" ]]; then
  printf 'Gitleaks is required. Run scripts/security/install_gitleaks.sh first.\n' >&2
  exit 2
fi

# Local secret-bearing files are permitted, but only when other users cannot
# read them. Examples are public by design and therefore excluded from this
# permission check (they remain content-scanned below).
permission_failure=0
while IFS= read -r -d '' candidate; do
  mode="$(stat -c '%a' "$candidate")"
  if (( (8#$mode & 077) != 0 )); then
    printf 'Secret-bearing file must be mode 0600 or stricter: %s (mode %s)\n' \
      "$candidate" "$mode" >&2
    permission_failure=1
  fi
done < <(
  find . \
    \( -path './.git' -o -path './.venv' -o -path '*/.venv' \
       -o -path './node_modules' -o -path '*/node_modules' \
       -o -path '*/dist' -o -path '*/build' \) -prune -o \
    -type f \
    \( -name '.env' -o -name '.env.local' -o -name '.env.*.local' \
       -o -name '*.pem' -o -name '*.key' \) \
    -print0
)
if (( permission_failure != 0 )); then
  exit 1
fi

report_dir="$(mktemp -d)"
clone_pid=""
cleanup() {
  if [[ -n "$clone_pid" ]] && kill -0 "$clone_pid" 2>/dev/null; then
    kill "$clone_pid" 2>/dev/null || true
    wait "$clone_pid" 2>/dev/null || true
  fi
  rm -r "$report_dir" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# History catches removed/renamed credentials; dir catches uncommitted and
# ignored local files. Full redaction is mandatory even in private CI logs.
# Pack every ref into one local bundle before scanning. Development workspaces
# commonly live on NFS with thousands of loose objects; copying a mirror or
# running `git log -p --all` in place turns that into minutes of random metadata
# I/O. ``git bundle --all`` performs one sequential pack, then the local clone
# gives Gitleaks a normal repository while preserving every referenced commit.
history_status=0
working_status=0
if (( scan_history != 0 )); then
  git bundle create "$report_dir/repository.bundle" --all &
  clone_pid=$!
  wait "$clone_pid"
  clone_pid=""
  git clone --quiet --mirror \
    "$report_dir/repository.bundle" "$report_dir/repository.git"
  "$gitleaks_bin" git \
    --no-banner \
    --redact=100 \
    --config .gitleaks.toml \
    --report-format json \
    --report-path "$report_dir/history.json" \
    "$report_dir/repository.git" || history_status=$?
fi
"$gitleaks_bin" dir \
  --no-banner \
  --redact=100 \
  --config .gitleaks.toml \
  --report-format json \
  --report-path "$report_dir/working-tree.json" \
  . || working_status=$?

if (( history_status != 0 || working_status != 0 )); then
  printf 'Secret scan failed (history=%s, working-tree=%s). Reports were redacted.\n' \
    "$history_status" "$working_status" >&2
  exit 1
fi
if (( scan_history != 0 )); then
  printf 'Secret scan passed for Git history and working tree.\n'
else
  printf 'Secret scan passed for working tree.\n'
fi

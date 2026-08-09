#!/usr/bin/env bash
# Fetch a PINNED, sha512-verified `runsc` (gVisor) static binary into a
# user-owned cache dir. No root. Prints the absolute path to the binary on
# stdout (and nothing else on stdout — diagnostics go to stderr) so callers
# can capture it directly:
#
#     RUNSC_PATH="$(bash scripts/get_runsc.sh)"
#
# Idempotent: if the cached binary already exists it is reused (no download).
#
# RE-6 P1 T2. runsc is NOT a pip dependency — it is an external static binary
# fetched at runtime (here / by the conftest fixture) so the guarded gVisor
# integration test (T3) actually RUNS rather than skipping.
set -euo pipefail

# --- Pin -------------------------------------------------------------------
# PINNED release (NOT `latest`) for supply-chain reproducibility. Bump this
# deliberately together with the image build and security review.
RUNSC_RELEASE="20260601.0"
ARCH="$(uname -m)"

PINNED_BASE="https://storage.googleapis.com/gvisor/releases/release/${RUNSC_RELEASE}/${ARCH}"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/vibecanvas/runsc"
RUNSC_BIN="${CACHE_DIR}/runsc"

log() { echo "[get_runsc] $*" >&2; }

# --- Fast path: already cached --------------------------------------------
if [[ -x "${RUNSC_BIN}" ]]; then
    log "cached runsc already present: ${RUNSC_BIN}"
    echo "${RUNSC_BIN}"
    exit 0
fi

mkdir -p "${CACHE_DIR}"

# Downloader (curl preferred, wget fallback).
_dl() {  # _dl <url> <dest>; returns nonzero on HTTP error / network failure
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 -o "${dest}" "${url}"
    else
        wget -q -O "${dest}" "${url}"
    fi
}

# --- Fetch + verify from a given base URL ----------------------------------
# Downloads runsc + runsc.sha512 from <base>, rewrites the checksum file so its
# filename column matches our local download name, then `sha512sum -c`.
_fetch_verify() {
    local base="$1"
    local tmp_bin tmp_sha
    tmp_bin="$(mktemp "${CACHE_DIR}/.runsc.XXXXXX")"
    tmp_sha="$(mktemp "${CACHE_DIR}/.runsc.sha512.XXXXXX")"
    # shellcheck disable=SC2064
    trap "rm -f '${tmp_bin}' '${tmp_sha}'" RETURN

    log "downloading ${base}/runsc"
    _dl "${base}/runsc" "${tmp_bin}" || return 1
    log "downloading ${base}/runsc.sha512"
    _dl "${base}/runsc.sha512" "${tmp_sha}" || return 1

    # gVisor's .sha512 file references the filename `runsc`; point it at our
    # temp download so `sha512sum -c` checks the right file.
    local expected
    expected="$(awk '{print $1}' "${tmp_sha}" | head -n1)"
    if [[ -z "${expected}" ]]; then
        log "empty/invalid checksum file from ${base}"
        return 1
    fi
    if ! echo "${expected}  ${tmp_bin}" | sha512sum -c - >&2; then
        log "sha512 verification FAILED for ${base}/runsc"
        return 1
    fi

    chmod +x "${tmp_bin}"
    mv -f "${tmp_bin}" "${RUNSC_BIN}"
    # tmp_bin moved away; clear trap target for it (tmp_sha still removed).
    trap "rm -f '${tmp_sha}'" RETURN
    return 0
}

if ! _fetch_verify "${PINNED_BASE}"; then
    log "ERROR: pinned runsc release-${RUNSC_RELEASE} is unavailable or invalid"
    exit 1
fi
log "installed PINNED runsc release-${RUNSC_RELEASE} -> ${RUNSC_BIN}"

echo "${RUNSC_BIN}"

#!/usr/bin/env bash
# Install and start Docker Engine from Docker's official APT repository.
#
# Supported host: Ubuntu 24.04 (Noble), amd64.
# The script is idempotent and never accepts a password on stdin. sudo prompts
# through the invoking terminal, so credentials are not written to disk.
set -euo pipefail
umask 022

readonly SUPPORTED_OS="ubuntu"
readonly SUPPORTED_CODENAME="noble"
readonly SUPPORTED_ARCH="amd64"

log() {
  printf '[docker-install] %s\n' "$*"
}

fail() {
  printf '[docker-install] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -ne 0 ]] || fail "Run this script as your normal login user; it invokes sudo when required."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."

# shellcheck disable=SC1091
source /etc/os-release
os_id="${ID:-}"
os_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
architecture="$(dpkg --print-architecture)"

# Docker Desktop owns its daemon inside a separate WSL2 VM. Installing a
# second system daemon in the distribution creates ambiguous sockets and
# storage, so stop before changing APT state when Desktop integration is live.
if command -v docker >/dev/null 2>&1; then
  docker_server_os="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)"
  if [[ "$docker_server_os" == *"Docker Desktop"* ]]; then
    fail "Docker Desktop WSL integration is already active; do not install a second Ubuntu Docker daemon."
  fi
fi

[[ "$os_id" == "$SUPPORTED_OS" ]] || fail "Unsupported distribution: ${os_id:-unknown}."
[[ "$os_codename" == "$SUPPORTED_CODENAME" ]] || \
  fail "Unsupported Ubuntu release: ${os_codename:-unknown}; expected Ubuntu 24.04 (Noble)."
[[ "$architecture" == "$SUPPORTED_ARCH" ]] || \
  fail "Unsupported architecture: $architecture; expected $SUPPORTED_ARCH."

for command_name in apt-get curl dpkg install sudo systemctl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "Required command is missing: $command_name"
done

conflicting_packages=()
for package_name in \
  docker.io docker-compose docker-compose-v2 docker-doc docker-buildx \
  podman-docker containerd runc; do
  if dpkg-query -W -f='${db:Status-Status}' "$package_name" 2>/dev/null \
      | grep -qx installed; then
    conflicting_packages+=("$package_name")
  fi
done
if (( ${#conflicting_packages[@]} > 0 )); then
  fail "Conflicting packages are installed: ${conflicting_packages[*]}. Remove them explicitly before continuing."
fi

log "Requesting sudo authorization"
sudo -v

temporary_directory="$(mktemp -d)"
cleanup() {
  rm -rf -- "$temporary_directory"
}
trap cleanup EXIT

log "Installing repository prerequisites"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl

log "Configuring Docker's official signed APT repository"
curl -fsSL --retry 3 \
  https://download.docker.com/linux/ubuntu/gpg \
  -o "$temporary_directory/docker.asc"
sudo install -d -m 0755 /etc/apt/keyrings
sudo install -m 0644 "$temporary_directory/docker.asc" /etc/apt/keyrings/docker.asc

cat >"$temporary_directory/docker.sources" <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $os_codename
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo install -m 0644 \
  "$temporary_directory/docker.sources" \
  /etc/apt/sources.list.d/docker.sources

log "Installing Docker Engine, Buildx, and Compose"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

log "Enabling and starting Docker"
sudo systemctl enable --now docker
sudo systemctl enable --now containerd

log "Granting the current user access to the Docker socket"
sudo usermod -aG docker "$USER"

log "Verifying the daemon and CLI plugins"
sudo docker version
sudo docker buildx version
sudo docker compose version
sudo docker run --rm hello-world

log "Verifying that the Docker daemon is running rootful"
docker_pid="$(sudo cat /run/docker.pid)"
[[ "$docker_pid" =~ ^[0-9]+$ ]] || fail "Docker returned an invalid daemon PID: $docker_pid"
docker_uid="$(awk '/^Uid:/ { print $2; exit }' "/proc/$docker_pid/status")"
[[ "$docker_uid" == "0" ]] || \
  fail "Docker daemon PID $docker_pid is not rootful (effective UID: ${docker_uid:-unknown})."
log "Rootful Docker daemon verified (PID $docker_pid, effective UID 0)"

cat <<'EOF'

Docker installation and rootful verification completed successfully.

The docker group grants root-equivalent access to the Docker daemon. Start a
new login session (or run `newgrp docker`) before using Docker without sudo.
EOF

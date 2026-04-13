#!/usr/bin/env bash
# Prepare Docker daemon and typical VisionDock/Jetson permissions during install.
# - Linux (Jetson/Ubuntu): start & enable docker; add user to docker + video groups.
# - macOS: cannot start the engine without Docker Desktop; optionally launch the app.
#
# Run from repo root: ./scripts/prepare_docker_and_permissions.sh
# May prompt for sudo on Linux.

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_ok() { echo -e "${GREEN}[ok]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[warn]${NC} $1"; }
log_info() { echo -e "${CYAN}[info]${NC} $1"; }

# After sudo, real user is passed in env (SUDO_USER is also set by sudo).
TARGET_USER="${_VD_TARGET_USER:-${SUDO_USER:-$USER}}"
if [[ -z "${TARGET_USER}" || "${TARGET_USER}" == "root" ]]; then
  TARGET_USER="$(logname 2>/dev/null || echo "")"
fi

OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
  log_info "macOS: Docker runs inside Docker Desktop (Linux VM). This script cannot enable the engine from the shell alone."
  if [[ -d "/Applications/Docker.app" ]]; then
    if docker info >/dev/null 2>&1; then
      log_ok "Docker is already reachable (docker info OK)."
    else
      log_info "Launching Docker Desktop (first launch may require accepting the license in the GUI)…"
      open -a Docker || log_warn "Could not open Docker.app"
      log_warn "Wait until Docker finishes starting, then run: docker info"
    fi
  else
    log_warn "Docker Desktop not found under /Applications/Docker.app — install from https://www.docker.com/products/docker-desktop/"
  fi
  exit 0
fi

if [[ "$OS" != "Linux" ]]; then
  log_warn "Unsupported OS: $OS — skip."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  log_warn "docker CLI not installed — install Docker Engine first."
  exit 0
fi

if [[ "$(uname -s)" == "Linux" && "${EUID:-$(id -u)}" -ne 0 ]]; then
  log_info "Requesting sudo for: systemctl docker, user groups (docker, video)…"
  exec sudo env "_VD_TARGET_USER=$(id -un)" bash "$SCRIPT_PATH" "$@"
fi

# Running as root (via sudo)
if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files 2>/dev/null | grep -q '^docker\.service'; then
    systemctl enable docker 2>/dev/null || true
    if systemctl start docker; then
      log_ok "docker.service started."
    else
      log_warn "docker.service start failed — check journalctl -u docker"
    fi
  else
    log_warn "docker.service not found (is Docker Engine installed?)"
  fi
else
  log_warn "systemctl not available — start Docker manually if needed."
fi

if id "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER" 2>/dev/null && log_ok "User '$TARGET_USER' added to group docker." || log_warn "Could not add docker group (already member or missing)."
  usermod -aG video "$TARGET_USER" 2>/dev/null && log_ok "User '$TARGET_USER' added to group video." || true
else
  log_warn "User '$TARGET_USER' not found — skip usermod."
fi

if docker info >/dev/null 2>&1; then
  log_ok "Docker engine responds to docker info."
else
  log_warn "docker info still fails. If you just added the user to group docker, log out and log in (or reboot), then try: docker info"
fi

echo ""
echo -e "${CYAN}Note:${NC} Group changes apply after a new login session. Until then you may use ${YELLOW}sudo docker${NC} or ${YELLOW}newgrp docker${NC}."

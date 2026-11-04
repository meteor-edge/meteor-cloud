#!/usr/bin/env bash
# Install MeteorCloud first-boot enrollment on this device.
# Does not clone the repository. Source:
#   https://github.com/meteor-edge/meteor-cloud
#
#   curl -fsSL https://raw.githubusercontent.com/meteor-edge/meteor-cloud/master/src/device-plane/agent/packaging/firstboot/install-meteorcli-firstboot.sh | sudo bash
#   curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli-firstboot.sh | sudo bash
#
# After the next reboot (or --now), the device installs meteorcli, runs
# request-token, and polls in the background until an administrator approves.
#
# Env: METEOR_DOMAIN, METEOR_API_KEY, METEOR_NAME, METEOR_WAIT_SECONDS,
# METEORCLI_RELEASE_REPO (default meteor-edge/meteor-cloud),
# METEOR_FIRSTBOOT_REF (git ref for raw files; default master).
set -euo pipefail

REPO_URL="https://github.com/meteor-edge/meteor-cloud"
REPO="${METEORCLI_RELEASE_REPO:-meteor-edge/meteor-cloud}"
REF="${METEOR_FIRSTBOOT_REF:-master}"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${REF}/src/device-plane/agent/packaging/firstboot"
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

RUN_NOW=0
usage() {
  cat <<EOF
Install first-boot enrollment (request-token, then background poll until approved).

Repo: ${REPO_URL}

Usage:
  curl -fsSL ${RAW_BASE}/install-meteorcli-firstboot.sh | sudo bash
  sudo METEOR_DOMAIN=cloud.example.com METEOR_API_KEY=key_... $0
  sudo $0 --now     # also run enrollment now (not only on next boot)

Options:
  --now        Start meteorcli-firstboot.service immediately
  -h, --help   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --now)
      RUN_NOW=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: run as root (sudo)." >&2
  echo "Repo: ${REPO_URL}" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "error: systemctl is required" >&2
  exit 1
fi

echo "MeteorCloud first-boot installer"
echo "  ${REPO_URL}"

fetch() {
  local name="$1"
  local dest="$2"
  local local_src="${SCRIPT_DIR}/${name}"
  if [[ -n "${SCRIPT_DIR}" && -f "${local_src}" ]]; then
    cp "${local_src}" "${dest}"
    return 0
  fi
  curl -fsSL "${RAW_BASE}/${name}" -o "${dest}"
}

WORKDIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

fetch "meteorcli-firstboot.sh" "${WORKDIR}/meteorcli-firstboot.sh"
fetch "meteorcli-firstboot.service" "${WORKDIR}/meteorcli-firstboot.service"
fetch "meteorcli-firstboot.env.example" "${WORKDIR}/meteorcli-firstboot.env.example"

install -m 0755 "${WORKDIR}/meteorcli-firstboot.sh" /usr/local/sbin/meteorcli-firstboot.sh
install -m 0644 "${WORKDIR}/meteorcli-firstboot.service" /etc/systemd/system/meteorcli-firstboot.service

ENV_FILE=/etc/meteorcli-firstboot.env
if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0600 "${WORKDIR}/meteorcli-firstboot.env.example" "${ENV_FILE}"
fi

if [[ -n "${METEOR_DOMAIN:-}" ]] || [[ -n "${METEOR_API_KEY:-}" ]] || [[ -n "${METEOR_NAME:-}" ]]; then
  umask 077
  {
    echo "# Written by install-meteorcli-firstboot.sh"
    echo "# ${REPO_URL}"
    echo "METEOR_DOMAIN=${METEOR_DOMAIN:-cloud.example.com}"
    echo "METEOR_API_KEY=${METEOR_API_KEY:-key_replace_me}"
    if [[ -n "${METEOR_NAME:-}" ]]; then
      echo "METEOR_NAME=${METEOR_NAME}"
    fi
    if [[ -n "${METEOR_WAIT_SECONDS:-}" ]]; then
      echo "METEOR_WAIT_SECONDS=${METEOR_WAIT_SECONDS}"
    fi
  } >"${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"
fi

systemctl daemon-reload
systemctl enable meteorcli-firstboot.service

echo "Installed /usr/local/sbin/meteorcli-firstboot.sh"
echo "Enabled meteorcli-firstboot.service"
echo "Edit domain and API key: sudo editor ${ENV_FILE}"
echo "Docs: ${REPO_URL}/tree/${REF}/src/device-plane/agent/packaging/firstboot"

if [[ "${RUN_NOW}" -eq 1 ]]; then
  echo "Starting enrollment now…"
  systemctl start meteorcli-firstboot.service
  echo "Logs: journalctl -u meteorcli-firstboot -f"
else
  echo "Reboot (or: sudo systemctl start meteorcli-firstboot) to enroll."
fi

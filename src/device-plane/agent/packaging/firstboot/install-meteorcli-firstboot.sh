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
  if command -v apt-get >/dev/null 2>&1; then
    echo "curl is missing; installing curl ca-certificates"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl ca-certificates
  fi
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "error: systemctl is required" >&2
  exit 1
fi

echo "== MeteorCloud first-boot installer =="
echo "Repo: ${REPO_URL}"
echo "Ref:  ${REF}"
echo

fetch() {
  local name="$1"
  local dest="$2"
  local local_src="${SCRIPT_DIR}/${name}"
  if [[ -n "${SCRIPT_DIR}" && -f "${local_src}" ]]; then
    echo "  ${name}: copy from ${local_src}"
    cp "${local_src}" "${dest}"
    return 0
  fi
  echo "  ${name}: download ${RAW_BASE}/${name}"
  curl -fsSL "${RAW_BASE}/${name}" -o "${dest}"
}

is_placeholder() {
  local val="${1:-}"
  [[ -z "${val}" || "${val}" == "cloud.example.com" || "${val}" == "key_replace_me" ]]
}

read_env_value() {
  local key="$1"
  local path="$2"
  local line
  [[ -f "${path}" ]] || return 0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ "${line}" == "${key}="* ]] || continue
    printf '%s' "${line#${key}=}"
    return 0
  done < "${path}"
}

show_settings() {
  local domain="${METEOR_DOMAIN:-}"
  local key="${METEOR_API_KEY:-}"
  local name="${METEOR_NAME:-}"
  local file_domain file_key file_name
  file_domain="$(read_env_value METEOR_DOMAIN "${ENV_FILE}")"
  file_key="$(read_env_value METEOR_API_KEY "${ENV_FILE}")"
  file_name="$(read_env_value METEOR_NAME "${ENV_FILE}")"
  is_placeholder "${domain}" && domain="${file_domain}"
  is_placeholder "${key}" && key="${file_key}"
  [[ -z "${name}" ]] && name="${file_name}"
  echo "  env file: ${ENV_FILE}"
  if is_placeholder "${domain}"; then
    echo "  domain:   MISSING  (set METEOR_DOMAIN or edit ${ENV_FILE})"
  else
    echo "  domain:   ${domain}"
  fi
  if is_placeholder "${key}"; then
    echo "  API key:  MISSING  (set METEOR_API_KEY or edit ${ENV_FILE})"
  else
    echo "  API key:  set (${key:0:4}… not printed)"
  fi
  if [[ -n "${name}" ]]; then
    echo "  name:     ${name}"
  else
    echo "  name:     (hostname)"
  fi
  if is_placeholder "${domain}" || is_placeholder "${key}"; then
    echo
    echo "Enrollment cannot start until domain and API key are set."
    echo "  sudo METEOR_DOMAIN=192.168.0.118:8000 METEOR_API_KEY=key_... $0 --now"
    echo "  or: sudo editor ${ENV_FILE}"
    echo "  or: put them in /boot/firmware/.meteorenv"
    return 1
  fi
  return 0
}

WORKDIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "[1/4] Fetching first-boot files"
fetch "meteorcli-firstboot.sh" "${WORKDIR}/meteorcli-firstboot.sh"
fetch "meteorcli-firstboot.service" "${WORKDIR}/meteorcli-firstboot.service"
fetch "meteorcli-firstboot.env.example" "${WORKDIR}/meteorcli-firstboot.env.example"

install -m 0755 "${WORKDIR}/meteorcli-firstboot.sh" /usr/local/sbin/meteorcli-firstboot.sh
install -m 0644 "${WORKDIR}/meteorcli-firstboot.service" /etc/systemd/system/meteorcli-firstboot.service
echo "  installed /usr/local/sbin/meteorcli-firstboot.sh"
echo "  installed /etc/systemd/system/meteorcli-firstboot.service"

ENV_FILE=/etc/meteorcli-firstboot.env
echo
echo "[2/4] Enrollment settings"
if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0600 "${WORKDIR}/meteorcli-firstboot.env.example" "${ENV_FILE}"
  echo "  created ${ENV_FILE} from example"
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
  echo "  wrote ${ENV_FILE} from this command's environment"
fi

SETTINGS_OK=0
show_settings && SETTINGS_OK=1

echo
echo "[3/4] Enable meteorcli-firstboot.service"
systemctl daemon-reload
systemctl enable meteorcli-firstboot.service
echo "  enabled (runs on boot until the device is approved)"

echo
if [[ "${RUN_NOW}" -eq 1 ]]; then
  echo "[4/4] Start enrollment now"
  systemctl restart meteorcli-firstboot.service
  echo "  started meteorcli-firstboot.service"
  echo "  waiting for the first log lines…"
  sleep 2
  journalctl -u meteorcli-firstboot -n 40 --no-pager --output=short-iso || true
  echo
  if systemctl is-active --quiet meteorcli-firstboot.service; then
    echo "Status: running (installs meteorcli if needed, then request-token, then polls until you approve)."
  else
    echo "Status: $(systemctl is-active meteorcli-firstboot.service || true) — look at the log above."
  fi
  if [[ "${SETTINGS_OK}" -ne 1 ]]; then
    echo "Status: domain/API key still missing; the unit will retry until they are set."
  fi
  echo
  echo "Watch live:  journalctl -u meteorcli-firstboot -f"
  echo "When pending: approve the device in the MeteorCloud console."
  echo "After approve: first-boot removes itself; meteorcli.service stays."
else
  echo "[4/4] Not starting yet (--now was not passed)"
  echo "  reboot, or: sudo systemctl start meteorcli-firstboot"
  echo "  then watch: journalctl -u meteorcli-firstboot -f"
  if [[ "${SETTINGS_OK}" -ne 1 ]]; then
    echo "  set domain and API key before that (see step 2)."
  fi
fi

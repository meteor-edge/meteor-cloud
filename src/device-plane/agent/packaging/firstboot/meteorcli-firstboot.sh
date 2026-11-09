#!/usr/bin/env bash
# First boot on a golden image: install meteorcli, submit request-token, poll
# until an administrator approves, then start meteorcli.service and remove
# this first-boot unit (not needed on later boots).
#
# Interactive (SSH): setup + submit, then return. Polling continues in
# meteorcli-firstboot.service. systemd: --poll (or INVOCATION_ID) waits in
# the background until approved.
#
# Copy to the device as /usr/local/sbin/meteorcli-firstboot.sh (see README.md).
set -euo pipefail

# systemd services do not set HOME; with set -u, ${HOME} would abort.
if [[ -z "${HOME:-}" ]]; then
  HOME="$(getent passwd "$(id -un)" | cut -d: -f6 || true)"
  HOME="${HOME:-/root}"
  export HOME
fi

CONFIG_DIR="${METEORCLI_CONFIG_DIR:-/etc/meteorcli}"
TOKEN_FILE="${CONFIG_DIR}/device-token"
CLAIM_FILE="${CONFIG_DIR}/claim-secret"
ENV_FILE="${METEOR_FIRSTBOOT_ENV:-/etc/meteorcli-firstboot.env}"
INSTALL_URL="${METEOR_INSTALL_URL:-https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh}"
WAIT_SECONDS="${METEOR_WAIT_SECONDS:-300}"
UNIT_PATH="${METEOR_FIRSTBOOT_UNIT:-/etc/systemd/system/meteorcli-firstboot.service}"
INSTALLED_SCRIPT="${METEOR_FIRSTBOOT_SCRIPT:-/usr/local/sbin/meteorcli-firstboot.sh}"
POLL_MODE=0
export METEORCLI_CONFIG_DIR="${CONFIG_DIR}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:${PATH}"

if [[ "${EUID}" -ne 0 && -z "${METEOR_FIRSTBOOT_ALLOW_NONROOT:-}" ]]; then
  echo "error: meteorcli-firstboot.sh must run as root (it writes ${CONFIG_DIR})." >&2
  echo "sudo drops export. Pass values on the same line:" >&2
  echo "  sudo METEOR_DOMAIN=192.168.0.118:8000 METEOR_API_KEY=key_... $0" >&2
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      METEOR_DOMAIN="${2:?--domain requires a host}"
      shift 2
      ;;
    --api-key)
      METEOR_API_KEY="${2:?--api-key requires a key}"
      shift 2
      ;;
    --name)
      METEOR_NAME="${2:?--name requires a value}"
      shift 2
      ;;
    --poll)
      POLL_MODE=1
      shift
      ;;
    -h|--help)
      echo "Usage: sudo METEOR_DOMAIN=host:port METEOR_API_KEY=key_... $0"
      echo "   or: sudo $0 --domain host:port --api-key key_..."
      echo "   or: put METEOR_DOMAIN / METEOR_API_KEY in .meteorenv (boot partition, cwd, or home)"
      echo "Interactive: install, request-token, then poll in meteorcli-firstboot.service."
      echo "systemd uses --poll (does not occupy the SSH session)."
      echo "sudo ignores a previous 'export'; put the vars after sudo, or use .meteorenv."
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      exit 2
      ;;
  esac
done

# systemd sets INVOCATION_ID; keep polling off the console.
if [[ -n "${INVOCATION_ID:-}" ]]; then
  POLL_MODE=1
fi

log() {
  local msg
  msg="$(date -Is) $*"
  echo "${msg}"
  if [[ -w /dev/console ]]; then
    echo "${msg}" >/dev/console 2>/dev/null || true
  elif [[ -w /dev/tty1 ]]; then
    echo "${msg}" >/dev/tty1 2>/dev/null || true
  fi
}

step() {
  log ""
  log "=== $* ==="
}

# Fill METEOR_* only when empty. CLI flags and the process environment win.
meteor_var_empty() {
  local val="${1:-}"
  [[ -z "${val}" || "${val}" == "cloud.example.com" || "${val}" == "key_replace_me" ]]
}

load_meteor_kv_file() {
  local path="$1"
  [[ -f "${path}" && -r "${path}" ]] || return 1
  local line key val loaded=0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" ]] && continue
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    line="${line#"${line%%[![:space:]]*}"}"
    if [[ "${line}" =~ ^export[[:space:]]+ ]]; then
      line="${line#export}"
      line="${line#"${line%%[![:space:]]*}"}"
    fi
    case "${line}" in
      METEOR_DOMAIN=*|METEOR_API_KEY=*|METEOR_NAME=*|METEOR_WAIT_SECONDS=*)
        key="${line%%=*}"
        val="${line#*=}"
        val="${val#"${val%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        if [[ "${val}" == \"*\" ]]; then
          val="${val#\"}"
          val="${val%\"}"
        elif [[ "${val}" == \'*\' ]]; then
          val="${val#\'}"
          val="${val%\'}"
        fi
        if meteor_var_empty "${val}"; then
          continue
        fi
        if meteor_var_empty "${!key:-}"; then
          printf -v "${key}" '%s' "${val}"
          loaded=1
        fi
        ;;
    esac
  done < "${path}"
  if [[ "${loaded}" -eq 1 ]]; then
    log "Loaded enrollment settings from ${path}"
  fi
}

load_meteorenv_candidates() {
  local p resolved="" seen=" "
  local -a candidates=("$@")
  for p in "${candidates[@]}"; do
    [[ -n "${p}" && -f "${p}" ]] || continue
    resolved="$(readlink -f "${p}" 2>/dev/null || true)"
    [[ -n "${resolved}" ]] || continue
    [[ "${seen}" == *" ${resolved} "* ]] && continue
    seen+="${resolved} "
    load_meteor_kv_file "${resolved}" || true
  done
}

add_boot_meteorenv_files() {
  local dir="$1"
  local -n _boot_files="$2"
  local name
  [[ -n "${dir}" && -d "${dir}" ]] || return 0
  for name in .meteorenv meteorenv meteorenv.txt .meteorenv.txt; do
    _boot_files+=("${dir}/${name}")
  done
}

boot_vfat_mounts() {
  local dev mp fstype
  while read -r dev mp fstype _; do
    case "${fstype}" in
      vfat|msdos|fat|exfat) printf '%s\n' "${mp}" ;;
    esac
  done < /proc/mounts
}

try_mount_boot_partition() {
  local mp="/run/meteorcli-boot"
  local dev already=""
  mkdir -p "${mp}"
  if findmnt -n "${mp}" >/dev/null 2>&1; then
    printf '%s\n' "${mp}"
    return 0
  fi
  for dev in /dev/disk/by-label/bootfs /dev/disk/by-label/boot /dev/mmcblk0p1 /dev/nvme0n1p1; do
    [[ -e "${dev}" ]] || continue
    already="$(findmnt -n -o TARGET "${dev}" 2>/dev/null || true)"
    if [[ -n "${already}" ]]; then
      printf '%s\n' "${already}"
      return 0
    fi
    if mount -o ro,nosuid,nodev,noexec "${dev}" "${mp}" 2>/dev/null; then
      BOOT_MOUNT_CLEANUP="${mp}"
      printf '%s\n' "${mp}"
      return 0
    fi
  done
  return 1
}

cleanup_boot_mount() {
  if [[ -n "${BOOT_MOUNT_CLEANUP:-}" ]]; then
    umount "${BOOT_MOUNT_CLEANUP}" 2>/dev/null || true
    BOOT_MOUNT_CLEANUP=""
  fi
}

load_dot_meteorenv() {
  local -a candidates=()
  local p home_sudo="" parent_cwd=""
  candidates+=("${PWD}/.meteorenv")
  p="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.meteorenv"
  candidates+=("${p}")
  if [[ -n "${SUDO_USER:-}" ]]; then
    home_sudo="$(getent passwd "${SUDO_USER}" | cut -d: -f6 || true)"
    if [[ -n "${home_sudo}" ]]; then
      candidates+=("${home_sudo}/.meteorenv")
    fi
    if [[ -n "${PPID:-}" ]]; then
      parent_cwd="$(readlink -f "/proc/${PPID}/cwd" 2>/dev/null || true)"
      if [[ -n "${parent_cwd}" ]]; then
        candidates+=("${parent_cwd}/.meteorenv")
      fi
    fi
  fi
  if [[ -n "${HOME:-}" ]]; then
    candidates+=("${HOME}/.meteorenv")
  fi
  load_meteorenv_candidates "${candidates[@]}"
}

load_boot_meteorenv() {
  local -a files=()
  local dir mp
  if ! meteor_var_empty "${METEOR_DOMAIN:-}" && ! meteor_var_empty "${METEOR_API_KEY:-}"; then
    return 0
  fi
  if [[ -n "${METEOR_BOOT_DIR:-}" ]]; then
    add_boot_meteorenv_files "${METEOR_BOOT_DIR}" files
  fi
  add_boot_meteorenv_files "/boot/firmware" files
  add_boot_meteorenv_files "/boot" files
  while IFS= read -r dir; do
    [[ -n "${dir}" ]] || continue
    add_boot_meteorenv_files "${dir}" files
  done < <(boot_vfat_mounts)
  load_meteorenv_candidates "${files[@]}"
  if ! meteor_var_empty "${METEOR_DOMAIN:-}" && ! meteor_var_empty "${METEOR_API_KEY:-}"; then
    return 0
  fi
  mp="$(try_mount_boot_partition || true)"
  if [[ -n "${mp}" ]]; then
    files=()
    add_boot_meteorenv_files "${mp}" files
    load_meteorenv_candidates "${files[@]}"
    cleanup_boot_mount
  fi
}

# sudo env_reset drops `export METEOR_*`. Read them from the calling shell.
fill_meteor_from_environ() {
  local env_path="/proc/${1}/environ"
  [[ -r "${env_path}" ]] || return 1
  local entry=""
  while IFS= read -r -d '' entry || [[ -n "${entry}" ]]; do
    case "${entry}" in
      METEOR_DOMAIN=*)
        [[ -z "${METEOR_DOMAIN:-}" ]] && METEOR_DOMAIN="${entry#METEOR_DOMAIN=}"
        ;;
      METEOR_API_KEY=*)
        [[ -z "${METEOR_API_KEY:-}" ]] && METEOR_API_KEY="${entry#METEOR_API_KEY=}"
        ;;
      METEOR_NAME=*)
        [[ -z "${METEOR_NAME:-}" ]] && METEOR_NAME="${entry#METEOR_NAME=}"
        ;;
      METEOR_WAIT_SECONDS=*)
        [[ -z "${METEOR_WAIT_SECONDS:-}" ]] && METEOR_WAIT_SECONDS="${entry#METEOR_WAIT_SECONDS=}"
        ;;
    esac
  done < "${env_path}"
}

inherit_from_sudo_caller() {
  [[ -n "${SUDO_USER:-}" ]] || return 0
  [[ -z "${METEOR_DOMAIN:-}" || -z "${METEOR_API_KEY:-}" ]] || return 0
  local pid="${PPID}" i
  for i in 1 2 3 4 5; do
    [[ -n "${pid}" && "${pid}" != "0" && "${pid}" != "1" ]] || break
    fill_meteor_from_environ "${pid}" || true
    if [[ -n "${METEOR_DOMAIN:-}" && -n "${METEOR_API_KEY:-}" ]]; then
      log "Picked up METEOR_DOMAIN and METEOR_API_KEY from the shell that ran sudo."
      return 0
    fi
    pid="$(ps -o ppid= -p "${pid}" 2>/dev/null | tr -d ' ' || true)"
  done
}

inherit_from_sudo_caller
load_dot_meteorenv
if [[ -f "${ENV_FILE}" ]]; then
  load_meteor_kv_file "${ENV_FILE}" || true
else
  log "No ${ENV_FILE} yet (will prompt, use environment, .meteorenv, or the boot partition)."
fi
load_boot_meteorenv
WAIT_SECONDS="${METEOR_WAIT_SECONDS:-${WAIT_SECONDS}}"

enrolled() {
  [[ -s "${TOKEN_FILE}" ]]
}

remove_firstboot() {
  # Do not `disable --now`: this process may *be* meteorcli-firstboot.service.
  # Start meteorcli first, then drop first-boot so the next reboot skips it.
  log "Enrollment complete; removing first-boot (not needed on the next reboot)."
  systemctl disable meteorcli-firstboot.service 2>/dev/null || true
  rm -f "${UNIT_PATH}"
  rm -f "${ENV_FILE}"
  if [[ -n "${INSTALLED_SCRIPT}" && -f "${INSTALLED_SCRIPT}" ]]; then
    rm -f "${INSTALLED_SCRIPT}"
  fi
  systemctl daemon-reload 2>/dev/null || true
  log "First-boot removed. meteorcli.service stays enabled."
}

start_agent_service() {
  touch "${CONFIG_DIR}/.enrolled"
  log "Device token stored at ${TOKEN_FILE}"
  systemctl enable meteorcli.service 2>/dev/null || true
  systemctl start meteorcli.service 2>/dev/null || true
  log "Enrolled and meteorcli.service is running in the background."
  log "Logs: journalctl -u meteorcli -f"
  log "Status: meteorcli status"
  remove_firstboot
}

if enrolled; then
  step "Already enrolled (${TOKEN_FILE} exists)"
  log "Starting meteorcli.service; not submitting another request."
  start_agent_service
  log "Done. Status: systemctl status meteorcli"
  exit 0
fi

wait_for_network() {
  local domain="${METEOR_DOMAIN:-}"
  local i
  log "Waiting for control plane at http://${domain}/health …"
  for i in $(seq 1 60); do
    if curl -fsS --max-time 3 "http://${domain}/health" >/dev/null 2>&1; then
      log "Reachable: http://${domain}/health"
      return 0
    fi
    if curl -fsS --max-time 3 "https://${domain}/health" >/dev/null 2>&1; then
      log "Reachable: https://${domain}/health"
      return 0
    fi
    log "Not reachable yet (${i}/60); retry in 5s…"
    sleep 5
  done
  log "warning: still unreachable after 5 minutes; continuing anyway"
}

ask_on_this_terminal() {
  local var="$1"
  local prompt="$2"
  if [[ -n "${!var:-}" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    echo "error: ${var} is unset. sudo drops a prior 'export'." >&2
    echo "Put METEOR_DOMAIN and METEOR_API_KEY in .meteorenv (or on the boot partition), or run:" >&2
    echo "  sudo METEOR_DOMAIN=192.168.0.118:8000 METEOR_API_KEY=key_... $0" >&2
    exit 1
  fi
  log "Type ${var} in this SSH/terminal session (not the HDMI console)."
  printf '%s' "${prompt}"
  read -r "${var}"
}

prompt_if_missing() {
  if [[ "${POLL_MODE}" -eq 1 ]]; then
    local i
    for i in $(seq 1 36); do
      if ! meteor_var_empty "${METEOR_DOMAIN:-}" && ! meteor_var_empty "${METEOR_API_KEY:-}"; then
        break
      fi
      log "Credentials not ready yet (${i}/36); re-reading boot partition and ${ENV_FILE}…"
      load_dot_meteorenv
      if [[ -f "${ENV_FILE}" ]]; then
        load_meteor_kv_file "${ENV_FILE}" || true
      fi
      load_boot_meteorenv
      if meteor_var_empty "${METEOR_DOMAIN:-}" || meteor_var_empty "${METEOR_API_KEY:-}"; then
        sleep 5
      fi
    done
    if meteor_var_empty "${METEOR_DOMAIN:-}" || meteor_var_empty "${METEOR_API_KEY:-}"; then
      echo "error: METEOR_DOMAIN and METEOR_API_KEY must be set (${ENV_FILE}, .meteorenv, or the boot partition) for background poll." >&2
      exit 1
    fi
    METEOR_NAME="${METEOR_NAME:-$(hostname)}"
    return 0
  fi
  if [[ -z "${METEOR_DOMAIN:-}" || -z "${METEOR_API_KEY:-}" ]]; then
    log "METEOR_DOMAIN or METEOR_API_KEY is empty."
    log "Checked environment, sudo caller, .meteorenv, ${ENV_FILE}, and the boot partition."
  fi
  ask_on_this_terminal METEOR_DOMAIN "Control-plane host (e.g. 192.168.0.118:8000): "
  ask_on_this_terminal METEOR_API_KEY "Enrollment API key (key_…): "
  METEOR_NAME="${METEOR_NAME:-$(hostname)}"
  log "Server (METEOR_DOMAIN)=${METEOR_DOMAIN}"
  log "Device name=${METEOR_NAME}"
  log "API key is set (not printed)"
}

find_meteorcli() {
  local p
  if command -v meteorcli >/dev/null 2>&1; then
    command -v meteorcli
    return 0
  fi
  for p in /usr/local/bin/meteorcli /usr/bin/meteorcli /opt/meteorcli/bin/meteorcli; do
    if [[ -x "${p}" ]]; then
      printf '%s\n' "${p}"
      return 0
    fi
  done
  for p in /home/*/.local/bin/meteorcli; do
    if [[ -x "${p}" ]]; then
      printf '%s\n' "${p}"
      return 0
    fi
  done
  return 1
}

ensure_meteorcli() {
  local bin
  if bin="$(find_meteorcli)"; then
    PATH="$(dirname "${bin}"):${PATH}"
    export PATH
    log "meteorcli already installed: ${bin} ($(meteorcli --version 2>/dev/null || echo unknown)); skipping install"
    return 0
  fi
  log "meteorcli not found; installing latest from"
  log "  ${INSTALL_URL}"
  export DEBIAN_FRONTEND=noninteractive
  if command -v apt-get >/dev/null 2>&1; then
    log "apt-get update / install curl python3 python3-venv python3-pip ca-certificates wireguard-tools…"
    apt-get update -qq
    apt-get install -y -qq curl python3 python3-venv python3-pip ca-certificates wireguard-tools
  fi
  log "Downloading and running install-meteorcli.sh --with-service (this can take a few minutes)…"
  curl -fsSL "${INSTALL_URL}" | bash -s -- --with-service
  log "meteorcli install finished: $(command -v meteorcli)"
}

configure_meteorcli() {
  install -d -m 0755 "${CONFIG_DIR}"
  log "meteorcli config --domain ${METEOR_DOMAIN} --api-key (hidden)"
  meteorcli config --domain "${METEOR_DOMAIN}" --api-key "${METEOR_API_KEY}"
  log "Checking API key (meteorcli test)…"
  if meteorcli test; then
    log "meteorcli test: ok"
  else
    log "warning: meteorcli test failed; will still request enrollment"
  fi
}

persist_firstboot_env() {
  umask 077
  {
    echo "# Written by meteorcli-firstboot.sh"
    echo "METEOR_DOMAIN=${METEOR_DOMAIN}"
    echo "METEOR_API_KEY=${METEOR_API_KEY}"
    echo "METEOR_NAME=${METEOR_NAME:-}"
    echo "METEOR_WAIT_SECONDS=${WAIT_SECONDS}"
  } >"${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"
  log "Saved enrollment settings to ${ENV_FILE}"
}

write_unit_file() {
  cat >"${UNIT_PATH}" <<'EOF'
[Unit]
Description=MeteorCloud first-boot enrollment (request-token + poll)
Documentation=https://github.com/meteor-edge/meteor-cloud
After=local-fs.target network-online.target
Wants=network-online.target
ConditionPathExists=!/etc/meteorcli/device-token

[Service]
Type=simple
Environment=METEORCLI_CONFIG_DIR=/etc/meteorcli
Environment=HOME=/root
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
EnvironmentFile=-/etc/meteorcli-firstboot.env
ExecStart=/usr/local/sbin/meteorcli-firstboot.sh --poll
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
}

install_firstboot_unit() {
  local src self_path unit_src
  self_path="$(readlink -f "${BASH_SOURCE[0]}")"
  src="$(readlink -f "${INSTALLED_SCRIPT}" 2>/dev/null || true)"
  if [[ "${self_path}" != "${src}" ]]; then
    install -m 0755 "${self_path}" "${INSTALLED_SCRIPT}"
    log "Installed ${INSTALLED_SCRIPT}"
  fi
  unit_src="$(dirname "${self_path}")/meteorcli-firstboot.service"
  if [[ -f "${unit_src}" ]]; then
    install -m 0644 "${unit_src}" "${UNIT_PATH}"
  else
    write_unit_file
  fi
}

submit_request() {
  export METEORCLI_CONFIG_DIR="${CONFIG_DIR}"
  if [[ -f "${CLAIM_FILE}" ]]; then
    log "Pending request already on disk (${CLAIM_FILE})."
    return 0
  fi
  log "Submitting enrollment request (meteorcli request-token --name ${METEOR_NAME} --wait 0)…"
  meteorcli request-token --name "${METEOR_NAME}" --wait 0
}

poll_until_enrolled() {
  export METEORCLI_CONFIG_DIR="${CONFIG_DIR}"
  log "Polling for admin approval every ${WAIT_SECONDS}s (meteorcli claim)."
  log "Approve in console: Fleet → API keys or Devices → pending enrollment."
  log "This runs in meteorcli-firstboot.service; you can close SSH."
  while ! enrolled; do
    if [[ -f "${CLAIM_FILE}" ]]; then
      if meteorcli claim --wait "${WAIT_SECONDS}"; then
        break
      fi
      log "Still pending. Will poll again."
    else
      log "No claim secret yet; submitting request-token…"
      if meteorcli request-token --name "${METEOR_NAME}" --wait 0; then
        if enrolled; then
          break
        fi
        log "Request submitted. Waiting for approval…"
      else
        log "request-token failed; retry in 30s…"
        sleep 30
      fi
    fi
  done
  if ! enrolled; then
    echo "error: enrollment did not produce ${TOKEN_FILE}." >&2
    exit 1
  fi
}

handoff_poll_to_background() {
  persist_firstboot_env
  local self_path src
  self_path="$(readlink -f "${BASH_SOURCE[0]}")"
  src="$(readlink -f "${INSTALLED_SCRIPT}" 2>/dev/null || true)"
  if [[ "${self_path}" != "${src}" ]]; then
    install -m 0755 "${self_path}" "${INSTALLED_SCRIPT}"
    log "Installed ${INSTALLED_SCRIPT}"
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl not found; polling with nohup (log: /var/log/meteorcli-firstboot.log)"
    nohup "${INSTALLED_SCRIPT}" --poll >>/var/log/meteorcli-firstboot.log 2>&1 &
    log "Background PID $!. You can close this session."
    return 0
  fi
  install_firstboot_unit
  systemctl daemon-reload
  systemctl enable meteorcli-firstboot.service
  systemctl restart meteorcli-firstboot.service
  log "Polling continues in meteorcli-firstboot.service (not this terminal)."
  log "Watch: journalctl -u meteorcli-firstboot -f"
  log "You can close this SSH session. Approve the device in the console."
}

if [[ "${POLL_MODE}" -eq 1 ]]; then
  log "MeteorCloud first-boot enrollment (background; resumes if already partly done)"
  log "Config dir: ${CONFIG_DIR}"
  step "1/5 Domain and API key"
  prompt_if_missing
  log "Server=${METEOR_DOMAIN}  name=${METEOR_NAME:-$(hostname)}  API key is set (not printed)"
  persist_firstboot_env
  step "2/5 Network"
  wait_for_network
  step "3/5 Install meteorcli"
  ensure_meteorcli
  step "4/5 Save config and request-token"
  configure_meteorcli
  step "5/5 Poll until an admin approves"
  poll_until_enrolled
  start_agent_service
  exit 0
fi

log "MeteorCloud first-boot enrollment"
log "This script will:"
log "  1. Wait for the network / control plane"
log "  2. Install meteorcli (with systemd service) if missing"
log "  3. Save server domain + enrollment API key"
log "  4. Run meteorcli request-token (does not wait for approval here)"
log "  5. Continue polling in meteorcli-firstboot.service (every ${WAIT_SECONDS}s)"
log "  6. After approve: start meteorcli.service, then remove first-boot"
log "Config dir: ${CONFIG_DIR}"
log "Watch: journalctl -u meteorcli-firstboot -f"

step "1/6 Domain and API key"
prompt_if_missing

step "2/6 Network"
wait_for_network

step "3/6 Install meteorcli"
ensure_meteorcli

step "4/6 Save config (${CONFIG_DIR})"
configure_meteorcli

step "5/6 Request token"
if ! submit_request; then
  log "warning: request-token did not finish; background poll will retry"
fi

step "6/6 Hand off poll to meteorcli-firstboot.service"
handoff_poll_to_background
log "Done in this terminal. Enrollment is running in the background."

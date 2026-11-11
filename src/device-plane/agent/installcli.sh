#!/usr/bin/env bash
# Install meteorcli from this directory onto a Linux device.
#
#   ./installcli.sh              Install for the current user (~/.local); no background service
#   sudo ./installcli.sh         System-wide (/opt + /usr/local/bin) and start meteorcli.service
#   sudo ./installcli.sh --no-service   System-wide, without the background service
#   ./installcli.sh --uninstall  Remove the install, credentials, and MQTT/WireGuard state
#                                (default config locations only, not METEORCLI_CONFIG_DIR)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_PREFIX="/opt/meteorcli"
SYSTEM_BIN="/usr/local/bin"
# cloud-init / systemd often run with HOME unset; keep set -u happy.
HOME="${HOME:-$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6)}"
HOME="${HOME:-/root}"
USER_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/meteorcli"
USER_BIN="${HOME}/.local/bin"

PREFIX="${PREFIX:-}"
BIN_DIR="${BIN_DIR:-}"
UNINSTALL=0
# auto: a root install starts meteorcli.service when systemd is running.
# 1 (--with-service): require it. 0 (--no-service): skip it.
WITH_SERVICE=auto

usage() {
  cat <<EOF
Install meteorcli from ${SCRIPT_DIR}

Usage:
  $0                      Install for this user (${USER_PREFIX}, ${USER_BIN}); no background service
  sudo $0                 Install system-wide (${SYSTEM_PREFIX}, ${SYSTEM_BIN}) and start
                          meteorcli.service, which connects once the device is registered
  sudo $0 --no-service    System-wide, without meteorcli.service
  $0 --uninstall          Remove the install, credentials, and MQTT/WireGuard state
                          (default config locations; a METEORCLI_CONFIG_DIR is listed, not removed)

Options:
  --prefix DIR     Virtualenv location
  --bin-dir DIR    Directory for the meteorcli symlink
  --with-service   Require meteorcli.service (the default for root when systemd runs;
                   fails instead of skipping when it cannot be installed)
  --no-service     Do not install or start meteorcli.service
  --uninstall      Remove the venv, symlink, unit, and ~/.config/meteorcli (and /etc/meteorcli as root)
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="${2:?--prefix requires a directory}"
      shift 2
      ;;
    --bin-dir)
      BIN_DIR="${2:?--bin-dir requires a directory}"
      shift 2
      ;;
    --with-service)
      WITH_SERVICE=1
      shift
      ;;
    --no-service)
      WITH_SERVICE=0
      shift
      ;;
    --uninstall)
      UNINSTALL=1
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

if [[ -z "${PREFIX}" ]]; then
  if [[ "${EUID}" -eq 0 ]]; then
    PREFIX="${SYSTEM_PREFIX}"
  else
    PREFIX="${USER_PREFIX}"
  fi
fi
if [[ -z "${BIN_DIR}" ]]; then
  if [[ "${EUID}" -eq 0 ]]; then
    BIN_DIR="${SYSTEM_BIN}"
  else
    BIN_DIR="${USER_BIN}"
  fi
fi

need_writable() {
  local path="$1"
  # The nearest existing ancestor decides: a fresh user has no ~/.local yet (mkdir -p creates it).
  while [[ ! -e "${path}" && "${path}" != / ]]; do
    path="$(dirname "${path}")"
  done
  [[ -w "${path}" ]]
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      if "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
        command -v "${candidate}"
        return 0
      fi
    fi
  done
  return 1
}

remove_config_dir() {
  local dir="$1"
  if [[ -d "${dir}" ]]; then
    rm -rf "${dir}"
    echo "Removed ${dir}"
  fi
}

bring_wg0_down() {
  # The helper refuses to run unless it is root, so a non-root uninstall goes through
  # its passwordless sudo rule. Failures are reported here, before the helper is removed.
  local helper=/usr/local/libexec/meteorcli-wg
  local -a sudo_cmd=()
  [[ "${EUID}" -eq 0 ]] || sudo_cmd=(sudo -n)
  if [[ -x "${helper}" ]] && "${sudo_cmd[@]}" "${helper}" down >/dev/null 2>&1; then
    return 0
  fi
  if command -v wg-quick >/dev/null 2>&1 && "${sudo_cmd[@]}" wg-quick down wg0 >/dev/null 2>&1; then
    return 0
  fi
  if ip link show wg0 >/dev/null 2>&1; then
    echo "warning: could not bring the WireGuard interface wg0 down; it is still up." >&2
  fi
}

uninstall() {
  local link="${BIN_DIR}/meteorcli"
  local user_home=""
  if [[ "${EUID}" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now meteorcli.service 2>/dev/null || true
    rm -f /etc/systemd/system/meteorcli.service
    systemctl daemon-reload 2>/dev/null || true
  fi
  bring_wg0_down
  if [[ -L "${link}" || -f "${link}" ]]; then
    rm -f "${link}"
    echo "Removed ${link}"
  fi
  if [[ -d "${PREFIX}" ]]; then
    rm -rf "${PREFIX}"
    echo "Removed ${PREFIX}"
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    rm -f /etc/sudoers.d/meteorcli-wireguard /usr/local/libexec/meteorcli-wg \
      /etc/sudoers.d/meteorcli-logs /usr/local/libexec/meteorcli-logs \
      /etc/sudoers.d/meteorcli-systemd /usr/local/libexec/meteorcli-systemd
    remove_config_dir /etc/meteorcli
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
      user_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6 || true)"
      if [[ -n "${user_home}" ]]; then
        remove_config_dir "${user_home}/.config/meteorcli"
      fi
    fi
  else
    remove_config_dir "${XDG_CONFIG_HOME:-$HOME/.config}/meteorcli"
  fi
  if [[ -n "${METEORCLI_CONFIG_DIR:-}" && -d "${METEORCLI_CONFIG_DIR}" ]]; then
    echo "note: METEORCLI_CONFIG_DIR=${METEORCLI_CONFIG_DIR} was not removed; it may hold a device token." >&2
  fi
}

install_root_helper() {
  # Install packaging/<name> root-owned as /usr/local/libexec/<name>, and let the
  # device user (when not root) run that one file with passwordless sudo.
  local name="$1" dest="$2" purpose="$3"
  local helper="/usr/local/libexec/${name}"
  local user="${SUDO_USER:-}"
  local python_bin tmp
  # A fixed, root-owned interpreter: one from $PATH (a venv, ~/.local/bin) would run as root.
  python_bin=/usr/bin/python3
  if [[ ! -x "${python_bin}" || "$(stat -Lc '%u' "${python_bin}")" != 0 ]] \
    || (( 8#$(stat -Lc '%a' "${python_bin}") & 8#022 )); then
    echo "warning: ${python_bin} is missing or not root-only; skipping ${helper}." >&2
    return 0
  fi
  install -d -o root -g root -m 0755 "$(dirname "${helper}")"
  tmp="$(mktemp)"
  { echo "#!${python_bin} -I"; tail -n +2 "${SCRIPT_DIR}/packaging/${name}"; } > "${tmp}"
  install -o root -g root -m 0755 "${tmp}" "${helper}"
  rm -f "${tmp}"
  if [[ -z "${user}" || "${user}" == root ]]; then
    return 0
  fi
  tmp="$(mktemp)"
  cat > "${tmp}" <<EOF
# meteorcli: ${purpose} (this helper only; see ${helper})
Defaults:${user} !requiretty
${user} ALL=(root) NOPASSWD: ${helper}
EOF
  chmod 440 "${tmp}"
  if visudo -cf "${tmp}" >/dev/null 2>&1; then
    mv "${tmp}" "${dest}"
    echo "Passwordless sudo for ${user}: ${helper}"
  else
    rm -f "${tmp}"
    echo "warning: could not install ${dest}; ${user} cannot run ${helper}." >&2
  fi
}

install_host_packages() {
  # WireGuard tunnel needs wg + wg-quick on PATH. Root installs pull them in;
  # a user-only install only prints a tip (no apt without sudo).
  [[ "${EUID}" -eq 0 ]] || return 0
  if command -v wg >/dev/null 2>&1 && command -v wg-quick >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    echo "Installing wireguard-tools (wg, wg-quick)…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq wireguard-tools
    return 0
  fi
  echo "warning: install wireguard-tools (wg + wg-quick) so meteorcli can bring up the device tunnel." >&2
}

install_root_helpers() {
  [[ "${EUID}" -eq 0 ]] || return 0
  # Creating wg0 needs CAP_NET_ADMIN. meteorcli run stays unprivileged and
  # calls `sudo -n meteorcli-wg` for those steps only. The helper is root-owned
  # and never passes the user's config (or its PostUp hooks) to wg-quick, so
  # this rule does not amount to passwordless root.
  install_root_helper meteorcli-wg /etc/sudoers.d/meteorcli-wireguard \
    'bring up wg0 without `sudo meteorcli`'
  # Read-only logs (journal, Docker, /var/log) for the console's Logs window and
  # `meteorcli logs`, without putting the user in the root-equivalent docker group.
  install_root_helper meteorcli-logs /etc/sudoers.d/meteorcli-logs 'read device logs'
  # Install / enable systemd units from a Deploy. The helper copies a
  # caller-owned .service file and runs fixed systemctl argv (not arbitrary shell).
  install_root_helper meteorcli-systemd /etc/sudoers.d/meteorcli-systemd \
    'install systemd units from meteorcli without `sudo meteorcli`'
}

# meteorcli.service always uses /etc/meteorcli. If the operator enrolled as a
# normal user (e.g. pi) before enabling the service, copy that config in.
migrate_user_config_to_system() {
  local system_dir="/etc/meteorcli"
  local user_home user_dir
  local copied=0

  mkdir -p "${system_dir}"
  chmod 0755 "${system_dir}"

  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    user_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6 || true)"
  fi
  if [[ -z "${user_home:-}" && -n "${HOME:-}" && "${HOME}" != "/root" ]]; then
    user_home="${HOME}"
  fi
  [[ -n "${user_home:-}" ]] || return 0

  user_dir="${user_home}/.config/meteorcli"
  if [[ ! -d "${user_dir}" ]]; then
    return 0
  fi

  # Migrate only into an unused system dir. Every root install (and reinstall) runs this,
  # and a device still waiting for approval has config.json/api-key/claim-secret but no
  # device-token yet: copying a stale ~/.config over that would change its server or key.
  local existing
  for existing in device-token config.json api-key claim-secret; do
    if [[ -f "${system_dir}/${existing}" ]]; then
      echo "Keeping existing ${system_dir} (${existing} already present)."
      return 0
    fi
  done
  if [[ ! -f "${user_dir}/device-token" && ! -f "${user_dir}/config.json" ]]; then
    return 0
  fi

  local name
  for name in config.json device-token api-key mqtt.json mqtt-ca.crt claim-secret; do
    if [[ -f "${user_dir}/${name}" ]]; then
      cp -a "${user_dir}/${name}" "${system_dir}/${name}"
      chmod 0600 "${system_dir}/${name}" 2>/dev/null || true
      copied=1
    fi
  done
  # Artifacts cache is optional; skip large trees unless empty on system side.
  if [[ -d "${user_dir}/artifacts" && ! -e "${system_dir}/artifacts" ]]; then
    cp -a "${user_dir}/artifacts" "${system_dir}/artifacts"
    copied=1
  fi

  if [[ "${copied}" -eq 1 ]]; then
    echo "Migrated meteorcli config from ${user_dir} → ${system_dir}"
    echo "(meteorcli.service reads /etc/meteorcli as root.)"
  fi
}

install_service() {
  local unit_src="${SCRIPT_DIR}/packaging/meteorcli.service"
  local unit_dst="/etc/systemd/system/meteorcli.service"
  if [[ "${EUID}" -ne 0 ]]; then
    echo "error: --with-service requires a root (system-wide) install." >&2
    exit 1
  fi
  if [[ ! -f "${unit_src}" ]]; then
    echo "error: missing ${unit_src}" >&2
    exit 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "error: systemctl not found; cannot install the service." >&2
    exit 1
  fi
  migrate_user_config_to_system
  # Do not call ``install`` here — a function of that name used to recurse forever.
  cp "${unit_src}" "${unit_dst}"
  chmod 0644 "${unit_dst}"
  # Point ExecStart at the installed binary when BIN_DIR differs.
  if [[ "${BIN_DIR}/meteorcli" != "/usr/local/bin/meteorcli" ]]; then
    sed -i "s|^ExecStart=.*|ExecStart=${BIN_DIR}/meteorcli run|" "${unit_dst}"
  fi
  systemctl daemon-reload
  systemctl enable meteorcli.service
  # restart, not start: an upgrade must replace the running agent with the new code.
  # Before registration the service only waits; it connects once the device is approved.
  systemctl restart meteorcli.service
  echo "Installed and started ${unit_dst} (enabled at boot)."
  echo "It waits for registration and connects (heartbeats, MQTT, WireGuard) once approved."
}

# Decide whether to install meteorcli.service. Prints the reason when skipping.
want_service() {
  case "${WITH_SERVICE}" in
    1) return 0 ;;
    0) return 1 ;;
  esac
  if [[ "${EUID}" -ne 0 ]]; then
    echo "note: no background service for a per-user install; this device only connects while" >&2
    echo "      'meteorcli run' is open. For meteorcli.service, install with sudo." >&2
    return 1
  fi
  # /run/systemd/system exists only when systemd is PID 1 (not in most containers or WSL1).
  if [[ ! -d /run/systemd/system ]] || ! command -v systemctl >/dev/null 2>&1; then
    echo "note: systemd is not running here; skipping meteorcli.service. Use 'meteorcli run'." >&2
    return 1
  fi
  return 0
}

install_meteorcli() {
  local python
  python="$(find_python)" || {
    echo "error: Python 3.11+ is required (python3, python3.11, python3.12, or python3.13)." >&2
    echo "On Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip" >&2
    exit 1
  }

  if ! "${python}" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    echo "error: the venv module is missing for ${python}." >&2
    echo "On Debian/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  fi

  echo "Using ${python} ($("${python}" --version 2>&1))"
  echo "Installing meteorcli into ${PREFIX}"

  mkdir -p "${PREFIX}" "${BIN_DIR}"
  "${python}" -m venv "${PREFIX}"
  "${PREFIX}/bin/python" -m pip install --upgrade pip
  "${PREFIX}/bin/python" -m pip install "${SCRIPT_DIR}"

  ln -sfn "${PREFIX}/bin/meteorcli" "${BIN_DIR}/meteorcli"
  if [[ "${EUID}" -eq 0 && "${BIN_DIR}" == "${SYSTEM_BIN}" ]]; then
    mkdir -p /etc/meteorcli
    chmod 0755 /etc/meteorcli
  fi

  echo
  "${BIN_DIR}/meteorcli" --version
  echo "Installed ${BIN_DIR}/meteorcli"
  if [[ "${EUID}" -ne 0 && ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
    echo "Add ${BIN_DIR} to PATH if meteorcli is not found."
  fi
  # Packages and helpers first: the service may bring up WireGuard as soon as it starts.
  install_host_packages
  install_root_helpers
  local service=0
  if want_service; then
    install_service
    service=1
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    if ! command -v wg >/dev/null 2>&1 || ! command -v wg-quick >/dev/null 2>&1; then
      echo "warning: wg/wg-quick still missing; device WireGuard will stay off until wireguard-tools is installed." >&2
    fi
    if [[ "${service}" -eq 1 ]]; then
      echo "Next: sudo meteorcli config --domain <host> --api-key key_..."
      echo "      sudo meteorcli test"
      echo "      sudo meteorcli request-token    (or: sudo meteorcli register --token ...)"
      echo "      meteorcli.service connects as soon as the device is approved; no 'meteorcli run' needed."
      echo "      Logs: journalctl -u meteorcli -f"
    else
      echo "Next: meteorcli config --domain <host> --api-key key_..."
      echo "      meteorcli test"
      echo "      meteorcli register|request-token, then meteorcli run"
    fi
  else
    echo "Next: meteorcli config --domain <host> --api-key key_..."
    echo "      meteorcli test"
    echo "      meteorcli register|request-token, then meteorcli run"
    echo "Tip: sudo ./installcli.sh installs wireguard-tools, meteorcli-wg, meteorcli-logs, and meteorcli-systemd."
    echo "     It also starts meteorcli.service, so the device connects without 'meteorcli run'."
  fi
}

if [[ "${UNINSTALL}" -eq 1 ]]; then
  if ! need_writable "${PREFIX}" || ! need_writable "${BIN_DIR}"; then
    echo "error: ${PREFIX} or ${BIN_DIR} is not writable; re-run with sudo for a system install." >&2
    exit 1
  fi
  uninstall
  exit 0
fi

if ! need_writable "${PREFIX}" || ! need_writable "${BIN_DIR}"; then
  echo "error: ${PREFIX} or ${BIN_DIR} is not writable; re-run with sudo for a system install." >&2
  exit 1
fi

install_meteorcli

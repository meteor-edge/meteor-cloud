#!/usr/bin/env bash
# Install meteorcli from this directory onto a Linux device.
#
#   ./installcli.sh              Install for the current user (~/.local)
#   sudo ./installcli.sh         System-wide (/opt + /usr/local/bin)
#   ./installcli.sh --uninstall  Remove the install (keeps credentials)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_PREFIX="/opt/meteorcli"
SYSTEM_BIN="/usr/local/bin"
USER_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/meteorcli"
USER_BIN="${HOME}/.local/bin"

PREFIX="${PREFIX:-}"
BIN_DIR="${BIN_DIR:-}"
UNINSTALL=0
WITH_SERVICE=0

usage() {
  cat <<EOF
Install meteorcli from ${SCRIPT_DIR}

Usage:
  $0                      Install for this user (${USER_PREFIX}, ${USER_BIN})
  sudo $0                 Install system-wide (${SYSTEM_PREFIX}, ${SYSTEM_BIN})
  sudo $0 --with-service  System-wide + enable meteorcli.service (Raspberry Pi OS)
  $0 --uninstall          Remove the install (keeps credentials)

Options:
  --prefix DIR     Virtualenv location
  --bin-dir DIR    Directory for the meteorcli symlink
  --with-service   Install and enable systemd unit (root / system install only)
  --uninstall      Remove the venv and symlink (and unit if present)
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
  local parent
  parent="$(dirname "${path}")"
  if [[ -e "${path}" ]]; then
    [[ -w "${path}" ]]
  else
    [[ -w "${parent}" ]]
  fi
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

uninstall() {
  local link="${BIN_DIR}/meteorcli"
  if [[ "${EUID}" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now meteorcli.service 2>/dev/null || true
    rm -f /etc/systemd/system/meteorcli.service
    systemctl daemon-reload 2>/dev/null || true
  fi
  if [[ -L "${link}" || -f "${link}" ]]; then
    rm -f "${link}"
    echo "Removed ${link}"
  fi
  if [[ -d "${PREFIX}" ]]; then
    rm -rf "${PREFIX}"
    echo "Removed ${PREFIX}"
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    rm -f /etc/sudoers.d/meteorcli-wireguard /usr/local/libexec/meteorcli-wg
    echo "Left /etc/meteorcli in place (device credentials)."
  else
    echo "Left ${XDG_CONFIG_HOME:-$HOME/.config}/meteorcli in place (device credentials)."
  fi
}

install_wireguard_sudoers() {
  # Creating wg0 needs CAP_NET_ADMIN. meteorcli run stays unprivileged and
  # calls `sudo -n meteorcli-wg` for those steps only. The helper is root-owned
  # and never passes the user's config (or its PostUp hooks) to wg-quick, so
  # this rule does not amount to passwordless root.
  local dest=/etc/sudoers.d/meteorcli-wireguard
  local helper=/usr/local/libexec/meteorcli-wg
  local user="${SUDO_USER:-}"
  local python_bin tmp
  [[ "${EUID}" -eq 0 ]] || return 0
  if [[ -z "${user}" || "${user}" == root ]]; then
    return 0
  fi
  python_bin="$(command -v python3 || echo /usr/bin/python3)"
  install -d -o root -g root -m 0755 "$(dirname "${helper}")"
  tmp="$(mktemp)"
  { echo "#!${python_bin} -I"; tail -n +2 "${SCRIPT_DIR}/packaging/meteorcli-wg"; } > "${tmp}"
  install -o root -g root -m 0755 "${tmp}" "${helper}"
  rm -f "${tmp}"
  tmp="$(mktemp)"
  cat > "${tmp}" <<EOF
# meteorcli: bring up wg0 without \`sudo meteorcli\` (this helper only; see ${helper})
Defaults:${user} !requiretty
${user} ALL=(root) NOPASSWD: ${helper}
EOF
  chmod 440 "${tmp}"
  if visudo -cf "${tmp}" >/dev/null 2>&1; then
    mv "${tmp}" "${dest}"
    echo "Passwordless sudo for ${user}: ${helper}"
  else
    rm -f "${tmp}"
    echo "warning: could not install ${dest}; meteorcli run as ${user} cannot create wg0." >&2
  fi
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

  # Prefer migrating when the system dir has no device credential yet.
  if [[ -f "${system_dir}/device-token" ]]; then
    echo "Keeping existing ${system_dir} (device-token already present)."
    return 0
  fi
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
  echo "Installed ${unit_dst} (enabled). Start after config: systemctl start meteorcli"
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
  if [[ "${WITH_SERVICE}" -eq 1 ]]; then
    install_service
  fi
  install_wireguard_sudoers
  if [[ "${EUID}" -eq 0 ]]; then
    echo "Next: meteorcli config --domain <host> --api-key key_..."
    echo "      meteorcli test"
    echo "      meteorcli register|request-token, then meteorcli run"
    echo "      (meteorcli.service uses /etc/meteorcli: sudo meteorcli register, then systemctl start meteorcli)"
  else
    echo "Next: meteorcli config --domain <host> --api-key key_..."
    echo "      meteorcli test"
    echo "      meteorcli register|request-token, then meteorcli run"
    echo "Tip: sudo ./installcli.sh adds passwordless sudo for wg/wg-quick so run does not need root."
    echo "     For meteorcli.service: sudo ./installcli.sh --with-service"
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

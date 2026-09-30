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

usage() {
  cat <<EOF
Install meteorcli from ${SCRIPT_DIR}

Usage:
  $0                      Install for this user (${USER_PREFIX}, ${USER_BIN})
  sudo $0                 Install system-wide (${SYSTEM_PREFIX}, ${SYSTEM_BIN})
  $0 --uninstall          Remove the install (keeps credentials)

Options:
  --prefix DIR    Virtualenv location
  --bin-dir DIR   Directory for the meteorcli symlink
  --uninstall     Remove the venv and symlink
  -h, --help      Show this help
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
  if [[ -L "${link}" || -f "${link}" ]]; then
    rm -f "${link}"
    echo "Removed ${link}"
  fi
  if [[ -d "${PREFIX}" ]]; then
    rm -rf "${PREFIX}"
    echo "Removed ${PREFIX}"
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    echo "Left /etc/meteorcli in place (device credentials)."
  else
    echo "Left ${XDG_CONFIG_HOME:-$HOME/.config}/meteorcli in place (device credentials)."
  fi
}

install() {
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
  echo "Next: meteorcli config --domain <host> --api-key key_..."
  echo "      meteorcli test"
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

install

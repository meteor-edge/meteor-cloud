#!/usr/bin/env bash
# Make this device (or an OS image built from it) enroll by itself on boot:
# installs meteorcli (when missing) and meteorcli-settings.service, which applies
# the settings Meteor Imager puts on the card (.meteorenv: Wi-Fi, host name, user,
# SSH) early in boot and hands the enrollment values to meteorcli. The agent then
# sends the enrollment request and waits for approval by itself, across reboots.
#
#   curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli-firstboot.sh | sudo bash
#   sudo METEOR_DOMAIN=cloud.example.com METEOR_API_KEY=key_... bash install-meteorcli-firstboot.sh
#   sudo bash install-meteorcli-firstboot.sh --image   # used by meteorcli-prepare-image.sh
#
# Env: METEOR_DOMAIN, METEOR_API_KEY, METEOR_NAME (enroll this device now),
# METEORCLI_BASE_URL (release files; default: the latest agent-v* release).
# Source: https://github.com/meteor-edge/meteor-cloud
set -euo pipefail

REPO="${METEORCLI_RELEASE_REPO:-meteor-edge/meteor-cloud}"
BASE_URL="${METEORCLI_BASE_URL:-https://github.com/${REPO}/releases/latest/download}"
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

IMAGE=0
usage() {
  cat <<EOF
Install meteorcli and the first-boot settings service.

Usage:
  curl -fsSL ${BASE_URL}/install-meteorcli-firstboot.sh | sudo bash
  sudo METEOR_DOMAIN=cloud.example.com METEOR_API_KEY=key_... $0

Options:
  --image      Install and enable only, do not start or enroll: each card written
               from this image brings its own settings (meteorcli-prepare-image.sh)
  --now        Accepted for older instructions; the agent starts after settings
  -h, --help   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) IMAGE=1 ;;
    --now) ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ "${EUID}" -eq 0 ]] || { echo "error: run as root (sudo)." >&2; exit 1; }
if ! command -v curl >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  echo "curl is missing; installing curl ca-certificates"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates
fi
command -v curl >/dev/null 2>&1 || { echo "error: curl is required" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "error: systemctl is required" >&2; exit 1; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

# A file next to this script (a release folder or a checkout) wins over the download.
fetch() {
  local name="$1"
  if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/${name}" ]]; then
    cp "${SCRIPT_DIR}/${name}" "${WORKDIR}/${name}"
  else
    curl -fsSL --retry 3 --connect-timeout 15 -o "${WORKDIR}/${name}" "${BASE_URL}/${name}"
  fi
}

echo "== MeteorCloud first boot =="
echo "[1/3] meteorcli"
# Never start the agent here: install-meteorcli.sh would restart meteorcli.service,
# whose ExecStartPre runs `meteorcli config meteorenv` and can delete the card's
# .meteorenv (Wi-Fi password included) before meteorcli-settings.service applies it.
args=(--no-start)
enroll_args=0
if [[ "${IMAGE}" -eq 0 ]]; then
  [[ -n "${METEOR_DOMAIN:-}" ]] && args+=(--domain "${METEOR_DOMAIN}") && enroll_args=1
  [[ -n "${METEOR_API_KEY:-}" ]] && args+=(--api-key "${METEOR_API_KEY}") && enroll_args=1
  [[ -n "${METEOR_NAME:-}" ]] && args+=(--name "${METEOR_NAME}") && enroll_args=1
fi
if command -v meteorcli >/dev/null 2>&1 && meteorcli agent --help >/dev/null 2>&1 && [[ "${enroll_args}" -eq 0 ]]; then
  echo "  installed: $(meteorcli --version)"
else
  fetch install-meteorcli.sh
  # A release folder next to this script holds the binaries too.
  if [[ -z "${METEORCLI_BASE_URL:-}" && -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/SHA256SUMS" ]]; then
    export METEORCLI_BASE_URL="file://${SCRIPT_DIR}"
  fi
  bash "${WORKDIR}/install-meteorcli.sh" --base-url "${METEORCLI_BASE_URL:-${BASE_URL}}" "${args[@]}"
fi

echo "[2/3] meteorcli-settings.service"
fetch meteorcli-settings.sh
fetch meteorcli-settings.service
install -m 0755 "${WORKDIR}/meteorcli-settings.sh" /usr/local/sbin/meteorcli-settings.sh
install -m 0644 "${WORKDIR}/meteorcli-settings.service" /etc/systemd/system/meteorcli-settings.service
systemctl daemon-reload
systemctl enable meteorcli-settings.service >/dev/null
echo "  installed and enabled (applies .meteorenv early on every boot)"
# The Python agent's first-boot service from an older image: the agent enrolls by itself now.
if [[ -f /etc/systemd/system/meteorcli-firstboot.service ]]; then
  systemctl disable --now meteorcli-firstboot.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/meteorcli-firstboot.service /usr/local/sbin/meteorcli-firstboot.sh
  systemctl daemon-reload
  echo "  removed the old meteorcli-firstboot.service"
fi

echo "[3/3] Meteor Imager marker"
# Tell Meteor Imager this image applies .meteorenv v2 itself (Wi-Fi, hostname,
# timezone, user, SSH), so it writes only .meteorenv and leaves cloud-init alone.
# Same partition order as meteorcli-settings.sh: METEORCFG first, then boot.
mark_config_partition() {
  local dir="" tmp="" cfg_dev=/dev/disk/by-label/METEORCFG cfg_dir
  if [[ -e "${cfg_dev}" ]]; then
    dir="$(findmnt -n -o TARGET "${cfg_dev}" 2>/dev/null | head -n 1 || true)"
    if [[ -z "${dir}" ]]; then
      tmp="$(mktemp -d)"
      if mount "${cfg_dev}" "${tmp}" 2>/dev/null; then
        dir="${tmp}"
      else
        rmdir "${tmp}"
        tmp=""
      fi
    fi
  fi
  if [[ -z "${dir}" ]]; then
    for cfg_dir in /boot/firmware /boot /boot/efi; do
      if [[ "$(findmnt -n -o FSTYPE --target "${cfg_dir}" 2>/dev/null || true)" == vfat ]]; then
        dir="${cfg_dir}"
        break
      fi
    done
  fi
  if [[ -z "${dir}" ]]; then
    echo "  warning: no FAT config partition; Meteor Imager will treat this as a stock image"
  elif printf 'meteor_settings=2\n' 2>/dev/null >"${dir}/.meteor-settings"; then
    echo "  marked ${dir}/.meteor-settings for Meteor Imager"
  else
    echo "  warning: could not write ${dir}/.meteor-settings (read-only?); Meteor Imager will treat this as a stock image"
  fi
  if [[ -n "${tmp}" ]]; then
    umount "${tmp}" 2>/dev/null || true
    rmdir "${tmp}" 2>/dev/null || true
  fi
  return 0
}
mark_config_partition

if [[ "${IMAGE}" -eq 1 ]]; then
  systemctl enable meteorcli-helper.socket meteorcli.service >/dev/null
  echo "Ready for an image: meteorcli starts on each card's first boot and enrolls with its .meteorenv."
else
  # Apply any card settings now (no-op when none), then start the agent so
  # ExecStartPre reads /etc/meteorcli/meteorenv instead of deleting the card file.
  systemctl start meteorcli-settings.service >/dev/null 2>&1 || true
  systemctl enable --now meteorcli-helper.socket meteorcli.service >/dev/null
  echo "Done. Without a key here, put .meteorenv (METEOR_DOMAIN, METEOR_API_KEY) on the boot partition"
  echo "and reboot. Approve the device in the console when the request appears: journalctl -u meteorcli -f"
fi

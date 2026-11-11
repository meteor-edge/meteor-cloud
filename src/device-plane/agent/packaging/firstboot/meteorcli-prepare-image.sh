#!/usr/bin/env bash
# Prepare this device to become an OS image for MeteorCloud: every card written
# from it boots as a new device that applies the settings Meteor Imager puts on
# the card (Wi-Fi, user, SSH, enrollment) and asks to be enrolled (request-token).
#
#   1. Flash a plain OS (e.g. Raspberry Pi OS Bookworm), boot, log in.
#   2. Install what every device needs (Docker, packages, meteorcli).
#   3. sudo meteorcli-prepare-image.sh      (powers off when done)
#   4. Back the card up with Meteor Imager, upload it as an os_image artifact.
#
# Removes this device's identity: meteorcli enrollment and keys, the first-boot
# env file, saved Wi-Fi networks, SSH host keys, machine-id, cloud-init state,
# shell history and logs. Installed software and Docker images stay.
#
# Source: https://github.com/meteor-edge/meteor-cloud
set -euo pipefail

ROOT="${METEOR_ROOT:-}"   # tests only: prefix for every system path
REPO="${METEORCLI_RELEASE_REPO:-meteor-edge/meteor-cloud}"
INSTALLER_URL="${METEOR_FIRSTBOOT_INSTALLER_URL:-https://github.com/${REPO}/releases/latest/download/install-meteorcli-firstboot.sh}"
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

YES=0
POWEROFF=1
KEEP_WIFI=0
ZERO_FILL=0

usage() {
  cat <<EOF
Prepare this device to become a MeteorCloud OS image (golden image).

Usage: sudo $0 [options]

Options:
  --yes               Do not ask for confirmation
  --no-poweroff       Stay on when done (default: power off, ready for backup)
  --keep-wifi         Keep saved Wi-Fi networks (their passwords end up in the image)
  --zero-free-space   Fill free space with zeros so an unshrunk backup compresses well
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --no-poweroff) POWEROFF=0 ;;
    --keep-wifi) KEEP_WIFI=1 ;;
    --zero-free-space) ZERO_FILL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${EUID}" -ne 0 && -z "${METEOR_PREPARE_ALLOW_NONROOT:-}" ]]; then
  echo "error: run as root (sudo)." >&2
  exit 1
fi

live() { [[ -z "${ROOT}" ]]; }
step() { echo; echo "== $*"; }
note() { echo "  $*"; }

boot_dir() {
  local d
  for d in "${ROOT}/boot/firmware" "${ROOT}/boot"; do
    if [[ -f "${d}/cmdline.txt" || -f "${d}/config.txt" || -f "${d}/.meteor-settings" ]]; then
      printf '%s\n' "${d}"
      return 0
    fi
  done
  return 1
}

BOOT="$(boot_dir || true)"

echo "MeteorCloud: prepare this device to become an OS image"
echo
echo "This removes, from this device:"
echo "  - meteorcli enrollment, device token, keys and VPN config (/etc/meteorcli)"
echo "  - first-boot enrollment settings (/etc/meteorcli-firstboot.env)"
[[ "${KEEP_WIFI}" -eq 1 ]] || echo "  - saved Wi-Fi networks and their passwords"
echo "  - SSH host keys (each card creates new ones), machine-id, cloud-init state"
echo "  - shell history, logs, apt caches"
echo "Installed software, users and Docker images stay."
if [[ -s "${ROOT}/etc/meteorcli/device-token" ]]; then
  echo
  echo "Note: this device is enrolled in MeteorCloud. Its record stays in the console;"
  echo "delete it there. Cards written from this image enroll as new devices."
fi
if [[ -z "${BOOT}" ]]; then
  echo
  echo "warning: no boot partition with cmdline.txt/config.txt under /boot/firmware or /boot;"
  echo "Meteor Imager will treat cards from this image like any other OS image."
fi
if [[ "${YES}" -ne 1 ]]; then
  if [[ ! -t 0 ]]; then
    echo "error: not a terminal; pass --yes to continue" >&2
    exit 1
  fi
  printf '\nType "prepare" to continue: '
  read -r answer
  [[ "${answer}" == prepare ]] || { echo "cancelled"; exit 1; }
fi

step "First-boot services (settings from the card, enrollment)"
if [[ ! -f "${ROOT}/usr/local/sbin/meteorcli-settings.sh" || ! -f "${ROOT}/usr/local/sbin/meteorcli-firstboot.sh" \
  || ! -f "${ROOT}/etc/systemd/system/meteorcli-firstboot.service" ]]; then
  live || { echo "error: first-boot services are not installed under ${ROOT}" >&2; exit 1; }
  if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/install-meteorcli-firstboot.sh" ]]; then
    bash "${SCRIPT_DIR}/install-meteorcli-firstboot.sh" --image
  else
    note "downloading ${INSTALLER_URL}"
    curl -fsSL "${INSTALLER_URL}" | bash -s -- --image
  fi
elif live; then
  systemctl enable meteorcli-firstboot.service meteorcli-settings.service >/dev/null
  note "already installed; enabled"
fi
if [[ -n "${BOOT}" && ! -f "${BOOT}/.meteor-settings" ]]; then
  printf 'meteor_settings=2\n' >"${BOOT}/.meteor-settings"
fi
[[ -n "${BOOT}" ]] && note "Meteor Imager marker: ${BOOT}/.meteor-settings"
if live && ! command -v meteorcli >/dev/null 2>&1; then
  note "meteorcli is not installed; each card installs it on first boot (needs internet)"
fi

step "meteorcli identity"
if live; then
  systemctl stop meteorcli.service meteorcli-firstboot.service 2>/dev/null || true
  ip link delete wg0 2>/dev/null || true
fi
if [[ -d "${ROOT}/etc/meteorcli" ]]; then
  find "${ROOT}/etc/meteorcli" -mindepth 1 -delete
  note "emptied /etc/meteorcli"
fi
rm -f "${ROOT}/etc/meteorcli-firstboot.env"
for home in "${ROOT}/root" "${ROOT}"/home/*; do
  [[ -d "${home}/.config/meteorcli" ]] && rm -rf "${home}/.config/meteorcli" && note "removed ${home#"${ROOT}"}/.config/meteorcli"
done
if [[ -n "$(ls -A "${ROOT}/var/lib/meteorcli/compose" 2>/dev/null || true)" ]]; then
  note "warning: apps deployed by MeteorCloud are still in /var/lib/meteorcli/compose; remove them first if cards must start empty"
fi

step "Network"
if [[ "${KEEP_WIFI}" -eq 1 ]]; then
  note "keeping Wi-Fi networks (--keep-wifi)"
else
  for f in "${ROOT}"/etc/NetworkManager/system-connections/*; do
    if [[ -f "${f}" ]] && grep -q '^type=wifi' "${f}"; then
      rm -f "${f}"
      note "removed Wi-Fi network ${f##*/}"
    fi
  done
  wpa="${ROOT}/etc/wpa_supplicant/wpa_supplicant.conf"
  if [[ -f "${wpa}" ]] && grep -q 'network=' "${wpa}"; then
    grep -E '^(ctrl_interface|update_config|country)=' "${wpa}" >"${wpa}.tmp" || true
    chmod 0600 "${wpa}.tmp"
    mv -f "${wpa}.tmp" "${wpa}"
    note "removed networks from wpa_supplicant.conf"
  fi
fi
# Written by cloud-init from the card's network-config; it holds the Wi-Fi password.
if [[ -f "${ROOT}/etc/netplan/50-cloud-init.yaml" ]]; then
  rm -f "${ROOT}/etc/netplan/50-cloud-init.yaml"
  note "removed /etc/netplan/50-cloud-init.yaml"
fi

step "cloud-init"
if [[ -d "${ROOT}/etc/cloud" ]]; then
  if live && command -v cloud-init >/dev/null 2>&1; then
    cloud-init clean --logs --seed >/dev/null 2>&1 || true
  fi
  rm -rf "${ROOT}/var/lib/cloud"
  # Cards get their settings from .meteorenv; cloud-init must not re-apply this
  # device's user-data (user, Wi-Fi) on every clone.
  touch "${ROOT}/etc/cloud/cloud-init.disabled"
  note "cloud-init cleaned and disabled"
fi
if [[ -n "${BOOT}" ]]; then
  for f in network-config wpa_supplicant.conf userconf.txt userconf ssh ssh.txt .meteorenv meteor-firstrun.sh firstrun.sh; do
    [[ -e "${BOOT}/${f}" ]] && rm -f "${BOOT}/${f}" && note "removed ${f} from the boot partition"
  done
  if [[ -f "${BOOT}/user-data" ]]; then
    printf '#cloud-config\n' >"${BOOT}/user-data"
    note "cleared user-data on the boot partition"
  fi
  if [[ -f "${BOOT}/cmdline.txt" ]] && grep -q 'ds=nocloud' "${BOOT}/cmdline.txt"; then
    sed -i -E 's/ ?ds=nocloud[^ ]*//g' "${BOOT}/cmdline.txt"
    note "removed ds=nocloud from cmdline.txt"
  fi
fi

step "Machine identity"
rm -f "${ROOT}"/etc/ssh/ssh_host_*
note "removed SSH host keys (each card creates its own on first boot)"
if [[ -e "${ROOT}/etc/machine-id" ]]; then
  : >"${ROOT}/etc/machine-id"
  note "cleared /etc/machine-id"
fi
[[ -L "${ROOT}/var/lib/dbus/machine-id" ]] || rm -f "${ROOT}/var/lib/dbus/machine-id"
touch "${ROOT}/etc/meteorcli-expand-rootfs"
note "root filesystem grows to the card on first boot"

step "Clean up"
rm -f "${ROOT}/root/.bash_history" "${ROOT}"/home/*/.bash_history
rm -rf "${ROOT}"/tmp/* "${ROOT}"/var/tmp/* 2>/dev/null || true
if live; then
  command -v apt-get >/dev/null 2>&1 && apt-get clean || true
  journalctl --rotate >/dev/null 2>&1 || true
  journalctl --vacuum-time=1s >/dev/null 2>&1 || true
fi
if [[ -d "${ROOT}/var/log" ]]; then
  find "${ROOT}/var/log" -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete 2>/dev/null || true
  find "${ROOT}/var/log" -type f -name '*.log' -exec truncate -s 0 {} + 2>/dev/null || true
fi
note "history, temp files, logs and apt cache cleared"

if [[ "${ZERO_FILL}" -eq 1 ]]; then
  step "Zero free space (this takes a while)"
  dd if=/dev/zero of="${ROOT}/meteor-zero.fill" bs=4M status=none 2>/dev/null || true
  sync
  rm -f "${ROOT}/meteor-zero.fill"
fi
sync

echo
echo "Done. Next:"
echo "  1. Put this card in your computer and open Meteor Imager > Back up a card."
echo "  2. Upload the backup in MeteorCloud > Artifacts as an os_image and tick"
echo "     \"Show in Meteor Imager\"."
echo "  3. Write cards from it with Meteor Imager (Wi-Fi, user and SSH on the General"
echo "     tab, enrollment on the MeteorCloud tab)."
if [[ "${POWEROFF}" -eq 1 ]] && live; then
  echo
  echo "Powering off in 5 seconds (Ctrl-C to stay on)…"
  sleep 5
  systemctl poweroff
else
  echo
  echo "Do not reboot before the backup: the next boot creates new keys and IDs."
  echo "New SSH logins fail until then (host keys removed). Shut down: sudo poweroff"
fi

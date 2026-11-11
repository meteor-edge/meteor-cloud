#!/bin/bash
# Copy this onto the Raspberry Pi bootfs next to .meteorenv and cmdline.txt.
# Raspberry Pi OS will not download MeteorCloud from .meteorenv alone.
#
# Also append this to the *single line* in cmdline.txt (do not add a newline):
#   systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.run_failure_action=reboot systemd.unit=kernel-command-line.target
# Older images: use /boot/firstrun.sh instead of /boot/firmware/firstrun.sh.
#
# This script only installs/enables meteorcli-firstboot.service, then ALWAYS
# restores a normal cmdline and reboots (success or failure) so SSH comes back.
# meteorcli is installed later by that service if it is missing.
set +e

INSTALL_URL="${METEOR_FIRSTBOOT_INSTALL_URL:-https://raw.githubusercontent.com/meteor-edge/meteor-cloud/master/src/device-plane/agent/packaging/firstboot/install-meteorcli-firstboot.sh}"

boot_dir() {
  local d
  for d in /boot/firmware /boot; do
    if [ -f "${d}/cmdline.txt" ]; then
      printf '%s\n' "${d}"
      return 0
    fi
  done
  printf '%s\n' /boot/firmware
}

BOOT="$(boot_dir)"
LOG="${BOOT}/meteorcli-firstrun.log"
FIRSTBOOT_SCRIPT="/usr/local/sbin/meteorcli-firstboot.sh"
UNIT_PATH="/etc/systemd/system/meteorcli-firstboot.service"
SETTINGS_SCRIPT="/usr/local/sbin/meteorcli-settings.sh"
SETTINGS_UNIT="/etc/systemd/system/meteorcli-settings.service"

log() {
  echo "$(date -Is 2>/dev/null || date) $*" | tee -a "${LOG}"
}

write_unit() {
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

enable_firstboot_service() {
  if [ ! -x "${FIRSTBOOT_SCRIPT}" ]; then
    return 1
  fi
  if [ -f "${BOOT}/meteorcli-firstboot.service" ]; then
    install -m 0644 "${BOOT}/meteorcli-firstboot.service" "${UNIT_PATH}"
  elif [ ! -f "${UNIT_PATH}" ]; then
    write_unit
  fi
  install_settings_service
  systemctl daemon-reload
  systemctl enable meteorcli-firstboot.service
  log "enabled meteorcli-firstboot.service"
  if [ -x "${SETTINGS_SCRIPT}" ] && [ -f "${SETTINGS_UNIT}" ]; then
    systemctl enable meteorcli-settings.service
    log "enabled meteorcli-settings.service"
  else
    log "meteorcli-settings.service not available; Wi-Fi, hostname, user and SSH in .meteorenv are not applied"
  fi
}

# Applies .meteorenv v2 (Wi-Fi, hostname, user, SSH) early in boot, like the installer does.
install_settings_service() {
  if [ -f "${BOOT}/meteorcli-settings.sh" ]; then
    install -m 0755 "${BOOT}/meteorcli-settings.sh" "${SETTINGS_SCRIPT}"
  fi
  if [ -f "${BOOT}/meteorcli-settings.service" ]; then
    install -m 0644 "${BOOT}/meteorcli-settings.service" "${SETTINGS_UNIT}"
  fi
}

install_from_bootfs() {
  if [ ! -f "${BOOT}/meteorcli-firstboot.sh" ]; then
    return 1
  fi
  log "installing first-boot script from ${BOOT} (no GitHub needed)"
  install -m 0755 "${BOOT}/meteorcli-firstboot.sh" "${FIRSTBOOT_SCRIPT}"
  enable_firstboot_service
}

ensure_curl() {
  if command -v curl >/dev/null 2>&1; then
    return 0
  fi
  log "curl missing; installing curl ca-certificates"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates
  command -v curl >/dev/null 2>&1
}

wait_for_network() {
  local i
  for i in $(seq 1 60); do
    if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 5 https://github.com >/dev/null 2>&1; then
      return 0
    fi
    if command -v wget >/dev/null 2>&1 && wget -q --timeout=5 -O /dev/null https://github.com >/dev/null 2>&1; then
      return 0
    fi
    log "waiting for network (${i}/60)"
    sleep 5
  done
  return 1
}

restore_normal_boot() {
  if [ -f "${BOOT}/cmdline.txt" ]; then
    sed -i 's| systemd.run=[^ ]*||g; s| systemd.run_success_action=[^ ]*||g; s| systemd.run_failure_action=[^ ]*||g; s| systemd.unit=[^ ]*||g' "${BOOT}/cmdline.txt"
    # If someone put systemd.run first, strip a leftover leading token too.
    sed -i 's|^systemd.run=[^ ]* ||' "${BOOT}/cmdline.txt"
  fi
  sync
}

cleanup_and_reboot() {
  restore_normal_boot
  log "rebooting into a normal boot (SSH should come back)"
  reboot
}

log "MeteorCloud firstrun starting (boot=${BOOT})"

if [ -s /etc/meteorcli/device-token ]; then
  log "already enrolled; skipping installer"
  rm -f "${BOOT}/firstrun.sh" /boot/firstrun.sh /boot/firmware/firstrun.sh
  cleanup_and_reboot
  exit 0
fi

if enable_firstboot_service; then
  log "meteorcli-firstboot.sh already on disk; not downloading again"
  rm -f "${BOOT}/firstrun.sh" /boot/firstrun.sh /boot/firmware/firstrun.sh
  cleanup_and_reboot
  exit 0
fi

if install_from_bootfs; then
  log "first-boot installed from bootfs"
  rm -f "${BOOT}/firstrun.sh" /boot/firstrun.sh /boot/firmware/firstrun.sh
  cleanup_and_reboot
  exit 0
fi

if ! ensure_curl; then
  log "error: could not install curl; restoring normal boot so SSH works"
  cleanup_and_reboot
  exit 0
fi
if ! wait_for_network; then
  log "error: no network/GitHub; restoring normal boot so SSH works"
  cleanup_and_reboot
  exit 0
fi
log "downloading installer from GitHub"
if curl -fsSL "${INSTALL_URL}" | bash; then
  log "installer ok"
  rm -f "${BOOT}/firstrun.sh" /boot/firstrun.sh /boot/firmware/firstrun.sh
else
  log "error: installer failed; restoring normal boot so SSH works"
fi
cleanup_and_reboot

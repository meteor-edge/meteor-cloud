#!/usr/bin/env bash
# Apply the settings Meteor Imager (or a person) put in .meteorenv on the card's
# config partition, early in boot, before the network starts. The file may also be
# called meteorenv.txt (the MeteorCloud console's download), meteorenv or .meteorenv.txt:
#
#   Wi-Fi (NetworkManager, or wpa_supplicant without it), hostname, timezone,
#   login user and password (as a crypt hash), SSH on/off, password login and an
#   SSH public key. The enrollment values (domain, key, name, API base) move to
#   /etc/meteorcli-firstboot.env (root only) for meteorcli-firstboot.service.
#
# Then .meteorenv is deleted from the card, so the key and Wi-Fi password are no
# longer readable by whoever holds it. Nothing found: exits 0 and changes nothing.
#
# On every boot, before that: SSH host keys are created when missing, and the root
# filesystem grows to the card once when meteorcli-prepare-image.sh asked for it.
#
# Config partition, first match: a FAT partition labelled METEORCFG (any device
# type), /boot/firmware (Raspberry Pi OS Bookworm+), /boot, /boot/efi (x86).
# Format: KEY=value lines, parsed (never sourced). See README.md.
set -euo pipefail

ROOT="${METEOR_ROOT:-}"   # tests only: prefix for every system path
ENV_FILE="${METEOR_FIRSTBOOT_ENV:-${ROOT}/etc/meteorcli-firstboot.env}"
TOKEN_FILE="${METEOR_DEVICE_TOKEN_FILE:-${ROOT}/etc/meteorcli/device-token}"
LOG_FILE="${METEOR_SETTINGS_LOG:-${ROOT}/var/log/meteorcli-settings.log}"
CFG_MOUNT="${METEOR_CFG_MOUNT:-/run/meteorcfg}"
NM_DIR="${ROOT}/etc/NetworkManager/system-connections"
EXPAND_FLAG="${ROOT}/etc/meteorcli-expand-rootfs"
SSHD_DROPIN="${ROOT}/etc/ssh/sshd_config.d/10-meteor.conf"
MOUNTED_CFG=""
SETTINGS_PATH=""

log() {
  local msg
  msg="$(date -Is 2>/dev/null || date) meteorcli-settings: $*"
  echo "${msg}"
  mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
  echo "${msg}" >>"${LOG_FILE}" 2>/dev/null || true
}

if [[ "${EUID}" -ne 0 && -z "${METEOR_SETTINGS_ALLOW_NONROOT:-}" ]]; then
  echo "error: run as root" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${MOUNTED_CFG}" ]]; then
    umount "${MOUNTED_CFG}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# --- find .meteorenv -------------------------------------------------------

# Sets SETTINGS_PATH. Not for $(...): MOUNTED_CFG must reach the EXIT trap.
find_settings_file() {
  local dir dev
  local -a dirs=()
  [[ -n "${METEOR_BOOT_DIR:-}" ]] && dirs+=("${METEOR_BOOT_DIR}")
  if [[ -z "${ROOT}" ]]; then
    dev="/dev/disk/by-label/METEORCFG"
    if [[ -e "${dev}" ]]; then
      dir="$(findmnt -n -o TARGET "${dev}" 2>/dev/null | head -n 1 || true)"
      if [[ -z "${dir}" ]]; then
        mkdir -p "${CFG_MOUNT}"
        if mount -o rw,nosuid,nodev,noexec "${dev}" "${CFG_MOUNT}" 2>/dev/null; then
          MOUNTED_CFG="${CFG_MOUNT}"
          dir="${CFG_MOUNT}"
        fi
      fi
      [[ -n "${dir}" ]] && dirs+=("${dir}")
    fi
  fi
  dirs+=("${ROOT}/boot/firmware" "${ROOT}/boot" "${ROOT}/boot/efi")
  # meteorenv.txt is what the MeteorCloud console downloads: dot files are hidden on
  # macOS and renamed by browsers, so a person copying the file by hand needs a plain name.
  local name
  for dir in "${dirs[@]}"; do
    for name in .meteorenv meteorenv.txt meteorenv .meteorenv.txt; do
      if [[ -f "${dir}/${name}" ]]; then
        SETTINGS_PATH="${dir}/${name}"
        return 0
      fi
    done
  done
  return 1
}

# --- parse -------------------------------------------------------------------

declare -A CFG=()
ALLOWED_KEYS=" METEOR_ENV_VERSION METEOR_DOMAIN METEOR_API_BASE METEOR_API_KEY METEOR_NAME METEOR_HOSTNAME METEOR_TIMEZONE METEOR_WIFI_SSID METEOR_WIFI_PSK METEOR_WIFI_COUNTRY METEOR_WIFI_HIDDEN METEOR_SSH_AUTHORIZED_KEY METEOR_SSH_USER METEOR_USER METEOR_USER_PASSWORD_HASH METEOR_SSH_ENABLED METEOR_SSH_PASSWORD_AUTH "

parse_settings() {
  local path="$1" line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    line="${line#$'\xef\xbb\xbf'}"   # a byte order mark from a Windows editor
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    if [[ "${ALLOWED_KEYS}" != *" ${key} "* ]]; then
      log "warning: ignoring unknown key ${key}"
      continue
    fi
    CFG["${key}"]="${val}"
  done <"${path}"
}

cfg() { printf '%s' "${CFG[$1]:-}"; }

# --- apply -------------------------------------------------------------------

apply_hostname() {
  local name
  name="$(cfg METEOR_HOSTNAME)"
  [[ -n "${name}" ]] || return 0
  if [[ ! "${name}" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
    log "warning: METEOR_HOSTNAME is not a valid host name; skipped"
    return 0
  fi
  local old=""
  [[ -f "${ROOT}/etc/hostname" ]] && old="$(head -n 1 "${ROOT}/etc/hostname")"
  printf '%s\n' "${name}" >"${ROOT}/etc/hostname" || return 1
  if [[ -f "${ROOT}/etc/hosts" ]] && grep -q '^127\.0\.1\.1' "${ROOT}/etc/hosts"; then
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t${name}/" "${ROOT}/etc/hosts" || return 1
  else
    printf '127.0.1.1\t%s\n' "${name}" >>"${ROOT}/etc/hosts" || return 1
  fi
  [[ -z "${ROOT}" ]] && hostname "${name}" 2>/dev/null || true
  log "hostname ${old:-?} -> ${name}"
}

apply_timezone() {
  local tz
  tz="$(cfg METEOR_TIMEZONE)"
  [[ -n "${tz}" ]] || return 0
  if [[ "${tz}" == *..* || ! -f "${ROOT}/usr/share/zoneinfo/${tz}" ]]; then
    log "warning: unknown timezone ${tz}; skipped"
    return 0
  fi
  ln -sf "/usr/share/zoneinfo/${tz}" "${ROOT}/etc/localtime" || return 1
  printf '%s\n' "${tz}" >"${ROOT}/etc/timezone" || return 1
  log "timezone ${tz}"
}

# GKeyFile string escaping for NetworkManager keyfiles.
keyfile_escape() {
  local v="$1"
  v="${v//\\/\\\\}"
  [[ "${v}" == " "* ]] && v="\\s${v:1}"
  printf '%s' "${v}"
}

set_wifi_country() {
  local country="$1" state
  # Raspberry Pi OS ships with Wi-Fi blocked until a country is set. systemd-rfkill
  # restores that saved state when the radio appears, which can be after this
  # early-boot run, so clear the saved state too (as Raspberry Pi Imager does).
  for state in "${ROOT}"/var/lib/systemd/rfkill/*:wlan; do
    if [[ -f "${state}" ]]; then
      echo 0 >"${state}" 2>/dev/null || true
    fi
  done
  if [[ -z "${ROOT}" ]] && command -v raspi-config >/dev/null 2>&1; then
    # Also unblocks Wi-Fi, which Raspberry Pi OS keeps blocked until a country is set.
    raspi-config nonint do_wifi_country "${country}" >/dev/null 2>&1 && return 0
  fi
  mkdir -p "${ROOT}/etc/modprobe.d" || return 1
  printf 'options cfg80211 ieee80211_regdom=%s\n' "${country}" >"${ROOT}/etc/modprobe.d/meteor-wifi-country.conf" || return 1
  if [[ -z "${ROOT}" ]]; then
    command -v iw >/dev/null 2>&1 && iw reg set "${country}" 2>/dev/null || true
    command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi 2>/dev/null || true
  fi
}

# Images can ship NetworkManager's files without running it (dhcpcd + wpa_supplicant),
# so go by whether the service is enabled, not by /etc/NetworkManager existing.
networkmanager_enabled() {
  local link="${ROOT}/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
  [[ -e "${link}" || -L "${link}" ]]
}

# Every write is checked: the caller runs this under `||`, where errexit is off,
# and .meteorenv (the only copy of the Wi-Fi password) is kept when it fails.
apply_wifi() {
  local ssid psk country hidden
  ssid="$(cfg METEOR_WIFI_SSID)"
  [[ -n "${ssid}" ]] || return 0
  psk="$(cfg METEOR_WIFI_PSK)"
  country="$(cfg METEOR_WIFI_COUNTRY)"
  hidden="$(cfg METEOR_WIFI_HIDDEN)"
  if (( ${#ssid} > 32 )); then
    log "warning: METEOR_WIFI_SSID is longer than 32 bytes; Wi-Fi skipped"
    return 0
  fi
  if [[ -n "${psk}" && ! ( ${#psk} -ge 8 && ${#psk} -le 63 ) && ! "${psk}" =~ ^[0-9a-fA-F]{64}$ ]]; then
    log "warning: METEOR_WIFI_PSK must be 8-63 characters; Wi-Fi skipped"
    return 0
  fi
  if [[ -n "${country}" ]]; then
    if [[ "${country}" =~ ^[A-Z]{2}$ ]]; then
      set_wifi_country "${country}" || return 1
    else
      log "warning: METEOR_WIFI_COUNTRY must be two capital letters; country skipped"
      country=""
    fi
  fi
  [[ "${hidden}" == "true" || "${hidden}" == "1" ]] && hidden=true || hidden=false

  if networkmanager_enabled; then
    local file="${NM_DIR}/meteor-wifi.nmconnection" uuid
    uuid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo 7d3c1b52-0b6e-4c55-9a1d-0f7e3c2b9a10)"
    mkdir -p "${NM_DIR}" || return 1
    (
      umask 077
      {
        echo "[connection]"
        echo "id=meteor-wifi"
        echo "uuid=${uuid}"
        echo "type=wifi"
        echo "autoconnect=true"
        echo ""
        echo "[wifi]"
        echo "mode=infrastructure"
        echo "ssid=$(keyfile_escape "${ssid}")"
        [[ "${hidden}" == true ]] && echo "hidden=true"
        echo ""
        if [[ -n "${psk}" ]]; then
          echo "[wifi-security]"
          echo "key-mgmt=wpa-psk"
          echo "psk=$(keyfile_escape "${psk}")"
          echo ""
        fi
        echo "[ipv4]"
        echo "method=auto"
        echo ""
        echo "[ipv6]"
        echo "method=auto"
      } >"${file}.tmp"
    ) || return 1
    chmod 0600 "${file}.tmp" || return 1
    mv -f "${file}.tmp" "${file}" || return 1
    if [[ -z "${ROOT}" ]] && command -v nmcli >/dev/null 2>&1 && nmcli -t general status >/dev/null 2>&1; then
      nmcli connection reload >/dev/null 2>&1 || true
      nmcli connection up meteor-wifi >/dev/null 2>&1 || true
    fi
    log "Wi-Fi ${ssid} added (NetworkManager, password not logged)"
    return 0
  fi

  local wpa="${ROOT}/etc/wpa_supplicant/wpa_supplicant.conf" ssid_hex key=""
  ssid_hex="$(printf '%s' "${ssid}" | od -An -tx1 | tr -d ' \n')"
  if [[ -n "${psk}" && ! "${psk}" =~ ^[0-9a-fA-F]{64}$ ]]; then
    # Store the derived key, not the passphrase.
    key="$(wpa_psk_hex "${ssid}" "${psk}" || true)"
    if [[ ! "${key}" =~ ^[0-9a-f]{64}$ ]]; then
      log "warning: could not derive the Wi-Fi key (needs python3 or wpa_passphrase)"
      return 1
    fi
  fi
  mkdir -p "$(dirname "${wpa}")" || return 1
  (
    umask 077
    {
      echo "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev"
      echo "update_config=1"
      [[ -n "${country}" ]] && echo "country=${country}"
      echo ""
      echo "network={"
      echo "	ssid=${ssid_hex}"
      [[ "${hidden}" == true ]] && echo "	scan_ssid=1"
      if [[ -z "${psk}" ]]; then
        echo "	key_mgmt=NONE"
      elif [[ -n "${key}" ]]; then
        echo "	psk=${key}"
      else
        echo "	psk=${psk}"
      fi
      echo "}"
    } >"${wpa}.tmp"
  ) || return 1
  chmod 0600 "${wpa}.tmp" || return 1
  mv -f "${wpa}.tmp" "${wpa}" || return 1
  log "Wi-Fi ${ssid} added (wpa_supplicant, password not logged)"
}

# PBKDF2-SHA1(passphrase, ssid, 4096, 32) as wpa_supplicant expects it.
wpa_psk_hex() {
  if command -v python3 >/dev/null 2>&1; then
    WPA_SSID="$1" WPA_PSK="$2" python3 -c 'import hashlib, os
print(hashlib.pbkdf2_hmac("sha1", os.environ["WPA_PSK"].encode(), os.environ["WPA_SSID"].encode(), 4096, 32).hex())'
  else
    wpa_passphrase "$1" "$2" | sed -n 's/^[[:space:]]*psk=\([0-9a-f]\{64\}\)$/\1/p'
  fi
}

ssh_user_home() {
  local user="$1"
  if [[ -n "${ROOT}" ]]; then
    awk -F: -v u="${user}" '$1 == u { print $6 }' "${ROOT}/etc/passwd"
  else
    getent passwd "${user}" | cut -d: -f6
  fi
}

default_ssh_user() {
  awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ { print $1; exit }' "${ROOT}/etc/passwd"
}

user_exists() {
  awk -F: -v u="$1" '$1 == u { found = 1 } END { exit !found }' "${ROOT}/etc/passwd"
}

# Groups Raspberry Pi OS gives its first user, where they exist on this image.
login_groups() {
  local g out=""
  for g in adm dialout cdrom sudo audio video plugdev games users input render netdev gpio i2c spi docker; do
    if awk -F: -v g="${g}" '$1 == g { found = 1 } END { exit !found }' "${ROOT}/etc/group" 2>/dev/null; then
      out="${out:+${out},}${g}"
    fi
  done
  printf '%s' "${out}"
}

# Like Raspberry Pi Imager: the image's first user (uid 1000) is renamed to the
# chosen name, keeping its groups (sudo, docker, ...) and files. Without one, a
# new user is created. The password arrives as a crypt hash, never as text.
apply_user() {
  local user hash first groups
  user="$(cfg METEOR_USER)"
  hash="$(cfg METEOR_USER_PASSWORD_HASH)"
  if [[ -z "${user}" ]]; then
    [[ -z "${hash}" ]] || log "warning: METEOR_USER_PASSWORD_HASH needs METEOR_USER; password skipped"
    return 0
  fi
  if [[ ! "${user}" =~ ^[a-z_][a-z0-9_-]{0,31}$ || "${user}" == root ]]; then
    log "warning: METEOR_USER is not a valid user name; skipped"
    return 0
  fi
  if [[ -n "${hash}" && ! "${hash}" =~ ^\$(6|5|y)\$[./A-Za-z0-9\$=,]+$ ]]; then
    log "warning: METEOR_USER_PASSWORD_HASH is not a crypt hash; password skipped"
    hash=""
  fi
  if ! user_exists "${user}"; then
    first="$(awk -F: '$3 == 1000 { print $1; exit }' "${ROOT}/etc/passwd")"
    if [[ -n "${first}" ]]; then
      if ! usermod -l "${user}" -d "/home/${user}" -m "${first}"; then
        log "warning: could not rename user ${first} to ${user}"
        return 1
      fi
      if awk -F: -v g="${first}" '$1 == g { found = 1 } END { exit !found }' "${ROOT}/etc/group" 2>/dev/null; then
        groupmod -n "${user}" "${first}" || log "warning: could not rename group ${first}"
      fi
      local f
      for f in "${ROOT}"/etc/sudoers.d/*; do
        if [[ -f "${f}" ]] && grep -q "^${first}[[:space:]]" "${f}"; then
          sed -i "s/^${first}\([[:space:]]\)/${user}\1/" "${f}"
        fi
      done
      log "user ${first} renamed to ${user}"
    else
      groups="$(login_groups)"
      if ! useradd -m -s /bin/bash ${groups:+-G "${groups}"} "${user}"; then
        log "warning: could not create user ${user}"
        return 1
      fi
      log "user ${user} created"
    fi
  fi
  if [[ -n "${hash}" ]]; then
    if usermod -p "${hash}" "${user}"; then
      log "password set for ${user} (not logged)"
    else
      log "warning: could not set the password for ${user}"
      return 1
    fi
  fi
}

ssh_service() {
  [[ -z "${ROOT}" ]] && command -v systemctl >/dev/null 2>&1 || return 0
  if [[ "$1" == enable ]]; then
    systemctl enable ssh >/dev/null 2>&1 || systemctl enable sshd >/dev/null 2>&1 || true
    systemctl start --no-block ssh >/dev/null 2>&1 || true
  else
    systemctl disable --now ssh >/dev/null 2>&1 || systemctl disable --now sshd >/dev/null 2>&1 || true
  fi
}

apply_ssh() {
  local enabled pwauth
  enabled="$(cfg METEOR_SSH_ENABLED)"
  pwauth="$(cfg METEOR_SSH_PASSWORD_AUTH)"
  case "${pwauth}" in
    "") ;;
    true|false)
      mkdir -p "$(dirname "${SSHD_DROPIN}")" || return 1
      # sshd keeps the first value it reads; 10- sorts before cloud-init's 50-.
      printf '# Written by meteorcli-settings.sh\nPasswordAuthentication %s\n' \
        "$([[ "${pwauth}" == true ]] && echo yes || echo no)" >"${SSHD_DROPIN}" || return 1
      log "SSH password login $([[ "${pwauth}" == true ]] && echo allowed || echo disabled)"
      ;;
    *) log "warning: METEOR_SSH_PASSWORD_AUTH must be true or false; skipped" ;;
  esac
  case "${enabled}" in
    "") ;;
    true) ssh_service enable && log "SSH enabled" ;;
    false) ssh_service disable && log "SSH disabled" ;;
    *) log "warning: METEOR_SSH_ENABLED must be true or false; skipped" ;;
  esac
}

apply_ssh_key() {
  local key user home ak
  key="$(cfg METEOR_SSH_AUTHORIZED_KEY)"
  [[ -n "${key}" ]] || return 0
  if [[ ! "${key}" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)\ [A-Za-z0-9+/=]+( .*)?$ ]]; then
    log "warning: METEOR_SSH_AUTHORIZED_KEY is not an OpenSSH public key; skipped"
    return 0
  fi
  user="$(cfg METEOR_SSH_USER)"
  [[ -n "${user}" ]] || user="$(cfg METEOR_USER)"
  [[ -n "${user}" ]] || user="$(default_ssh_user || true)"
  if [[ -z "${user}" ]]; then
    log "warning: no login user on this image for the SSH key; skipped"
    return 0
  fi
  home="$(ssh_user_home "${user}" || true)"
  if [[ -z "${home}" ]]; then
    log "warning: user ${user} does not exist; SSH key skipped"
    return 0
  fi
  ak="${ROOT}${home}/.ssh/authorized_keys"
  mkdir -p "$(dirname "${ak}")" || return 1
  touch "${ak}" || return 1
  grep -qxF "${key}" "${ak}" || printf '%s\n' "${key}" >>"${ak}" || return 1
  chmod 0700 "$(dirname "${ak}")" || return 1
  chmod 0600 "${ak}" || return 1
  [[ -z "${ROOT}" ]] && chown -R "${user}:" "$(dirname "${ak}")" 2>/dev/null || true
  # A key means "let me in": enable SSH unless .meteorenv turned it off.
  [[ "$(cfg METEOR_SSH_ENABLED)" == false ]] || ssh_service enable
  log "SSH key added for ${user}"
}

# A prepared image ships without host keys so every card gets its own.
ensure_ssh_host_keys() {
  [[ -x "${ROOT}/usr/sbin/sshd" ]] || return 0
  compgen -G "${ROOT}/etc/ssh/ssh_host_*_key" >/dev/null && return 0
  if ssh-keygen -A ${ROOT:+-f "${ROOT}"} >/dev/null 2>&1; then
    log "SSH host keys created"
  else
    log "warning: could not create SSH host keys"
  fi
}

# Grow the root partition and filesystem to the card, once, after a prepared
# image (often shrunk by Meteor Imager's backup) is written to a larger card.
expand_rootfs_once() {
  [[ -f "${EXPAND_FLAG}" ]] || return 0
  # Removed first: a failure must not repeat on every boot.
  rm -f "${EXPAND_FLAG}"
  [[ -z "${ROOT}" ]] || return 0
  local src name disk num last
  src="$(findmnt -n -o SOURCE /)"
  name="$(basename "${src}")"
  disk="$(lsblk -no PKNAME "${src}" 2>/dev/null | head -n 1)"
  num="$(cat "/sys/class/block/${name}/partition" 2>/dev/null || true)"
  if [[ -z "${disk}" || -z "${num}" ]]; then
    log "warning: root ${src} is not a partition; not resized"
    return 0
  fi
  last="$(lsblk -lno NAME,TYPE "/dev/${disk}" | awk '$2 == "part" { n = $1 } END { print n }')"
  if [[ "${last}" != "${name}" ]]; then
    log "warning: root ${src} is not the last partition; not resized"
    return 0
  fi
  if ! command -v sfdisk >/dev/null 2>&1 || ! command -v resize2fs >/dev/null 2>&1; then
    log "warning: sfdisk or resize2fs missing; root filesystem not resized"
    return 0
  fi
  if echo ", +" | sfdisk --no-reread --no-tell-kernel -q -N "${num}" "/dev/${disk}" >/dev/null 2>&1 \
    && partx -u -n "${num}" "/dev/${disk}" \
    && resize2fs "${src}" >/dev/null 2>&1; then
    log "root filesystem ${src} grown to the card"
  else
    log "warning: could not grow ${src}; run: sudo raspi-config nonint do_expand_rootfs"
  fi
}

save_enrollment() {
  local domain key
  domain="$(cfg METEOR_DOMAIN)"
  key="$(cfg METEOR_API_KEY)"
  if [[ -z "${domain}" && -z "${key}" ]]; then
    return 0
  fi
  if [[ -s "${TOKEN_FILE}" ]]; then
    # First boot already ran and removed itself; do not leave the key on disk.
    log "This device is already enrolled; the enrollment key in .meteorenv was not kept"
    return 0
  fi
  if [[ -z "${domain}" || "${key}" != key_* ]]; then
    log "warning: .meteorenv needs METEOR_DOMAIN and METEOR_API_KEY=key_… for enrollment"
  fi
  mkdir -p "$(dirname "${ENV_FILE}")"
  (
    umask 077
    {
      echo "# Written by meteorcli-settings.sh from the boot partition"
      echo "METEOR_DOMAIN=${domain}"
      echo "METEOR_API_KEY=${key}"
      [[ -n "$(cfg METEOR_NAME)" ]] && echo "METEOR_NAME=$(cfg METEOR_NAME)"
      [[ -n "$(cfg METEOR_API_BASE)" ]] && echo "METEOR_API_BASE=$(cfg METEOR_API_BASE)"
      true
    } >"${ENV_FILE}.tmp"
  )
  chmod 0600 "${ENV_FILE}.tmp"
  mv -f "${ENV_FILE}.tmp" "${ENV_FILE}"
  sync "${ENV_FILE}" 2>/dev/null || sync
  log "Enrollment settings saved to ${ENV_FILE} (key not logged)"
}

remove_settings_file() {
  local path="$1" mp
  if rm -f "${path}" 2>/dev/null && [[ ! -e "${path}" ]]; then
    sync
    log "Removed ${path}"
    return 0
  fi
  # Boot partition mounted read-only: remount it read-write for the delete.
  mp="$(findmnt -n -o TARGET --target "${path}" 2>/dev/null || true)"
  if [[ -n "${mp}" ]] && mount -o remount,rw "${mp}" 2>/dev/null && rm -f "${path}"; then
    sync
    log "Removed ${path}"
    return 0
  fi
  log "warning: could not remove ${path}; the key and Wi-Fi password stay readable on the card"
}

main() {
  local path step failed=0
  expand_rootfs_once || log "warning: resizing the root filesystem failed"
  ensure_ssh_host_keys || log "warning: SSH host keys failed"
  find_settings_file || exit 0
  path="${SETTINGS_PATH}"
  log "Applying ${path}"
  parse_settings "${path}"
  # Save first: enrollment still works if a setting below fails.
  save_enrollment
  for step in hostname timezone wifi user ssh ssh_key; do
    if ! "apply_${step}"; then
      log "warning: ${step} failed"
      failed=1
    fi
  done
  if [[ "${failed}" -eq 1 ]]; then
    # Every step is safe to repeat; the next boot tries again.
    log "warning: ${path} kept so the failed settings are retried on the next boot;" \
      "it still holds the Wi-Fi password and enrollment key"
    return 0
  fi
  remove_settings_file "${path}"
}

main "$@"

#!/usr/bin/env bash
# Apply the settings Meteor Imager (or a person) put in .meteorenv on the card's
# config partition, early in boot, before the network starts:
#
#   Wi-Fi (NetworkManager, or wpa_supplicant without it), hostname, timezone and
#   an SSH public key. The enrollment values (domain, key, name, API base) move
#   to /etc/meteorcli-firstboot.env (root only) for meteorcli-firstboot.service.
#
# Then .meteorenv is deleted from the card, so the key and Wi-Fi password are no
# longer readable by whoever holds it. Nothing found: exits 0 and changes nothing.
#
# Config partition, first match: a FAT partition labelled METEORCFG (any device
# type), /boot/firmware (Raspberry Pi OS Bookworm+), /boot, /boot/efi (x86).
# Format: KEY=value lines, parsed (never sourced). See README.md.
set -euo pipefail

ROOT="${METEOR_ROOT:-}"   # tests only: prefix for every system path
ENV_FILE="${METEOR_FIRSTBOOT_ENV:-${ROOT}/etc/meteorcli-firstboot.env}"
LOG_FILE="${METEOR_SETTINGS_LOG:-${ROOT}/var/log/meteorcli-settings.log}"
CFG_MOUNT="${METEOR_CFG_MOUNT:-/run/meteorcfg}"
NM_DIR="${ROOT}/etc/NetworkManager/system-connections"
MOUNTED_CFG=""

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
  for dir in "${dirs[@]}"; do
    if [[ -f "${dir}/.meteorenv" ]]; then
      printf '%s\n' "${dir}/.meteorenv"
      return 0
    fi
  done
  return 1
}

# --- parse -------------------------------------------------------------------

declare -A CFG=()
ALLOWED_KEYS=" METEOR_ENV_VERSION METEOR_DOMAIN METEOR_API_BASE METEOR_API_KEY METEOR_NAME METEOR_HOSTNAME METEOR_TIMEZONE METEOR_WIFI_SSID METEOR_WIFI_PSK METEOR_WIFI_COUNTRY METEOR_WIFI_HIDDEN METEOR_SSH_AUTHORIZED_KEY METEOR_SSH_USER "

parse_settings() {
  local path="$1" line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
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
  printf '%s\n' "${name}" >"${ROOT}/etc/hostname"
  if [[ -f "${ROOT}/etc/hosts" ]] && grep -q '^127\.0\.1\.1' "${ROOT}/etc/hosts"; then
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t${name}/" "${ROOT}/etc/hosts"
  else
    printf '127.0.1.1\t%s\n' "${name}" >>"${ROOT}/etc/hosts"
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
  ln -sf "/usr/share/zoneinfo/${tz}" "${ROOT}/etc/localtime"
  printf '%s\n' "${tz}" >"${ROOT}/etc/timezone"
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
  local country="$1"
  if [[ -z "${ROOT}" ]] && command -v raspi-config >/dev/null 2>&1; then
    # Also unblocks Wi-Fi, which Raspberry Pi OS keeps blocked until a country is set.
    raspi-config nonint do_wifi_country "${country}" >/dev/null 2>&1 && return 0
  fi
  mkdir -p "${ROOT}/etc/modprobe.d"
  printf 'options cfg80211 ieee80211_regdom=%s\n' "${country}" >"${ROOT}/etc/modprobe.d/meteor-wifi-country.conf"
  if [[ -z "${ROOT}" ]]; then
    command -v iw >/dev/null 2>&1 && iw reg set "${country}" 2>/dev/null || true
    command -v rfkill >/dev/null 2>&1 && rfkill unblock wifi 2>/dev/null || true
  fi
}

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
      set_wifi_country "${country}"
    else
      log "warning: METEOR_WIFI_COUNTRY must be two capital letters; country skipped"
      country=""
    fi
  fi
  [[ "${hidden}" == "true" || "${hidden}" == "1" ]] && hidden=true || hidden=false

  if [[ -d "${ROOT}/etc/NetworkManager" ]]; then
    local file="${NM_DIR}/meteor-wifi.nmconnection" uuid
    uuid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo 7d3c1b52-0b6e-4c55-9a1d-0f7e3c2b9a10)"
    mkdir -p "${NM_DIR}"
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
    )
    chmod 0600 "${file}.tmp"
    mv -f "${file}.tmp" "${file}"
    if [[ -z "${ROOT}" ]] && command -v nmcli >/dev/null 2>&1 && nmcli -t general status >/dev/null 2>&1; then
      nmcli connection reload >/dev/null 2>&1 || true
      nmcli connection up meteor-wifi >/dev/null 2>&1 || true
    fi
    log "Wi-Fi ${ssid} added (NetworkManager, password not logged)"
    return 0
  fi

  local wpa="${ROOT}/etc/wpa_supplicant/wpa_supplicant.conf" ssid_hex
  ssid_hex="$(printf '%s' "${ssid}" | od -An -tx1 | tr -d ' \n')"
  mkdir -p "$(dirname "${wpa}")"
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
      elif [[ "${psk}" =~ ^[0-9a-fA-F]{64}$ ]]; then
        echo "	psk=${psk}"
      else
        # Store the derived key, not the passphrase.
        echo "	psk=$(wpa_psk_hex "${ssid}" "${psk}")"
      fi
      echo "}"
    } >"${wpa}.tmp"
  )
  chmod 0600 "${wpa}.tmp"
  mv -f "${wpa}.tmp" "${wpa}"
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

apply_ssh_key() {
  local key user home ak
  key="$(cfg METEOR_SSH_AUTHORIZED_KEY)"
  [[ -n "${key}" ]] || return 0
  if [[ ! "${key}" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)\ [A-Za-z0-9+/=]+( .*)?$ ]]; then
    log "warning: METEOR_SSH_AUTHORIZED_KEY is not an OpenSSH public key; skipped"
    return 0
  fi
  user="$(cfg METEOR_SSH_USER)"
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
  mkdir -p "$(dirname "${ak}")"
  touch "${ak}"
  grep -qxF "${key}" "${ak}" || printf '%s\n' "${key}" >>"${ak}"
  chmod 0700 "$(dirname "${ak}")"
  chmod 0600 "${ak}"
  if [[ -z "${ROOT}" ]]; then
    chown -R "${user}:" "$(dirname "${ak}")" 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1; then
      systemctl enable ssh >/dev/null 2>&1 || systemctl enable sshd >/dev/null 2>&1 || true
      systemctl start --no-block ssh >/dev/null 2>&1 || true
    fi
  fi
  log "SSH key added for ${user}"
}

save_enrollment() {
  local domain key
  domain="$(cfg METEOR_DOMAIN)"
  key="$(cfg METEOR_API_KEY)"
  if [[ -z "${domain}" && -z "${key}" ]]; then
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
  local path
  if ! path="$(find_settings_file)"; then
    exit 0
  fi
  log "Applying ${path}"
  parse_settings "${path}"
  # Save first: enrollment still works if a setting below fails.
  save_enrollment
  apply_hostname || log "warning: hostname failed"
  apply_timezone || log "warning: timezone failed"
  apply_wifi || log "warning: Wi-Fi failed"
  apply_ssh_key || log "warning: SSH key failed"
  remove_settings_file "${path}"
}

main "$@"

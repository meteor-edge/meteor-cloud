#!/usr/bin/env bash
# Replace meteorcli with a newer published release, or put the previous one back.
#
# The live install is left in place until the new tree reports the expected
# version. meteorcli.service is stopped only for the switch. If that service
# was running and does not stay active, the previous tree is restored and
# started. Device config (/etc/meteorcli or the user config dir) is never
# written. A checksum file is required; a missing or wrong SHA256 aborts
# before anything is replaced.
#
#   sudo meteorcli update --check
#   sudo meteorcli update --yes
#   curl -fsSL .../update-meteorcli.sh | sudo bash -s -- --yes
#
# Exit 0 when nothing needs to change, or after a successful update.
# Exit 10 from --check when a newer release is available.
# Exit 1 when the update cannot be done safely.

set -euo pipefail

REPO="${METEORCLI_RELEASE_REPO:-meteor-edge/meteor-cloud}"
DIST_URL="${METEORCLI_DIST_URL:-}"
PREFIX="${PREFIX:-}"
BIN_DIR="${BIN_DIR:-}"
TO=""
YES=0
CHECK=0
ROLLBACK=0
ALLOW_DOWNGRADE=0
EXPLICIT=0
DOWNGRADING=0
WAS_ACTIVE=0
STOPPED=0
SWAPPED=0
MOVED_ASIDE=0
TARGET=""
CURRENT_VERSION=""
WORKDIR=""
AGENT_DIR=""
INSTALLER=""
PYTHON=""
STATE_DIR=""
HOME="${HOME:-$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6)}"
HOME="${HOME:-/root}"

HELPER_NAMES=(meteorcli-wg meteorcli-logs meteorcli-systemd meteorcli-wifi meteorcli-action)

usage() {
  cat <<EOF
Update meteorcli in place, keeping the previous install for rollback.

Usage:
  sudo $0 --check                 Print whether a newer release exists (exit 10 if so)
  sudo $0 --yes                   Install the newest agent-vX.Y.Z release
  sudo $0 --to 0.3.4 --yes        Install that version
  sudo $0 --rollback --yes        Restore the previous install

Device config is not modified. The new install is built beside the current one
and switched in only after \`meteorcli --version\` matches. If meteorcli.service
was running and does not stay active, the previous install is restored.

Options:
  --check            Do not change the device
  --yes              Proceed without a prompt (required when stdin is not a terminal)
  --to VERSION       Install X.Y.Z instead of the newest release
  --allow-downgrade  Allow --to / METEORCLI_VERSION to replace a newer install
  --rollback         Restore ${PREFIX:-the install} kept from the last update
  --prefix DIR       Virtualenv location (default: /opt/meteorcli as root)
  --bin-dir DIR      Directory of the meteorcli symlink
  -h, --help         Show this help

Environment:
  METEORCLI_VERSION       Same as --to (latest, X.Y.Z, or agent-vX.Y.Z)
  METEORCLI_DIST_URL      Tarball URL; SHA256SUMS must be in the same directory
  METEORCLI_RELEASE_REPO  GitHub owner/name (default: meteor-edge/meteor-cloud)
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

valid_version() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

version_is_newer() {
  # True when $1 is a higher version than $2.
  local newer
  newer="$(printf '%s\n%s\n' "$1" "$2" | LC_ALL=C sort -V | tail -n 1)"
  [[ "$newer" == "$1" && "$1" != "$2" ]]
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

latest_version_from_json() {
  local python="$1"
  "$python" -c '
import json, re, sys
pattern = re.compile(r"^agent-v(\d+\.\d+\.\d+)$")

def key(version):
    return tuple(int(part) for part in version.split("."))

releases = json.load(sys.stdin)
if isinstance(releases, dict):
    releases = [releases]
best = None
for release in releases:
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        continue
    match = pattern.match(release.get("tag_name") or "")
    if not match:
        continue
    version = match.group(1)
    if best is None or key(version) > key(best):
        best = version
if best is None:
    raise SystemExit(1)
print(best)
'
}

fetch_latest_version() {
  local json
  json="$(curl -fsSL --retry 3 --retry-delay 2 \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: meteorcli-update" \
    "https://api.github.com/repos/${REPO}/releases?per_page=30")" || return 1
  printf '%s' "$json" | latest_version_from_json "$PYTHON"
}

verify_checksum() {
  local sums="$1" file="$2" name="$3" expected actual
  expected="$(awk -v name="$name" '$2 == name || $2 == ("*" name) { print $1; exit }' "$sums")"
  [[ -n "$expected" ]] || die "SHA256SUMS has no entry for ${name}"
  actual="$(sha256sum "$file" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || die "checksum mismatch for ${name}"
  echo "checksum ok (${name})"
}

read_packaged_version() {
  local py="$1" init="$2" ver_py ver_init
  ver_py="$("$PYTHON" -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$py")"
  ver_init="$(sed -n -E 's/^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' "$init" | head -n 1)"
  [[ -n "$ver_py" && -n "$ver_init" ]] || die "could not read the meteorcli version in the tarball"
  [[ "$ver_py" == "$ver_init" ]] || die "tarball version mismatch (pyproject ${ver_py}, package ${ver_init})"
  valid_version "$ver_py" || die "tarball version ${ver_py} is not X.Y.Z"
  printf '%s\n' "$ver_py"
}

download_release() {
  local base name py init packaged
  if [[ -z "$DIST_URL" ]]; then
    base="https://github.com/${REPO}/releases/download/agent-v${TARGET}"
    DIST_URL="${base}/meteorcli-${TARGET}.tar.gz"
    SUMS_URL="${base}/SHA256SUMS"
  else
    SUMS_URL="${DIST_URL%/*}/SHA256SUMS"
  fi
  echo "downloading ${DIST_URL}"
  curl -fsSL --retry 3 --retry-delay 2 -H "User-Agent: meteorcli-update" \
    "$DIST_URL" -o "${WORKDIR}/meteorcli.tar.gz" || die "download failed: ${DIST_URL}"
  curl -fsSL --retry 3 --retry-delay 2 -H "User-Agent: meteorcli-update" \
    "$SUMS_URL" -o "${WORKDIR}/SHA256SUMS" || die "refusing to update without SHA256SUMS (${SUMS_URL})"
  name="$(basename "${DIST_URL%%\?*}")"
  verify_checksum "${WORKDIR}/SHA256SUMS" "${WORKDIR}/meteorcli.tar.gz" "$name"
  tar -xzf "${WORKDIR}/meteorcli.tar.gz" -C "$WORKDIR"
  py="$(find "$WORKDIR" -path '*/agent/pyproject.toml' -type f | head -n 1)"
  init="$(find "$WORKDIR" -path '*/agent/meteorcli/__init__.py' -type f | head -n 1)"
  [[ -n "$py" && -n "$init" ]] || die "tarball is missing meteorcli version files"
  packaged="$(read_packaged_version "$py" "$init")"
  if [[ -n "$TARGET" && "$TARGET" != "$packaged" ]]; then
    die "tarball version ${packaged} does not match requested ${TARGET}"
  fi
  TARGET="$packaged"
  AGENT_DIR="$(dirname "$py")"
  INSTALLER="${AGENT_DIR}/installcli.sh"
  [[ -f "$INSTALLER" ]] || die "tarball did not contain agent/installcli.sh"
}

ensure_free_space() {
  local parent avail
  parent="$(dirname "$PREFIX")"
  mkdir -p "$parent"
  avail="$(df -Pk "$parent" | awk 'NR==2 {print $4}')"
  [[ "$avail" =~ ^[0-9]+$ ]] || die "could not read free space on ${parent}"
  if [[ "$avail" -lt 204800 ]]; then
    die "need at least 200 MB free on ${parent} (have $((avail / 1024)) MB). The previous install is kept until the next update."
  fi
}

stage_venv() {
  # Called as `stage_venv || fail`, which turns off errexit for the whole function.
  local source_dir="$1" dest="$2" python
  python="$(find_python)" || return 1
  rm -rf "$dest"
  "$python" -m venv "$dest" || return 1
  "$dest/bin/python" -m pip install --disable-pip-version-check --upgrade pip || return 1
  "$dest/bin/python" -m pip install --disable-pip-version-check "$source_dir" || return 1
}

fix_venv_paths() {
  # venv console scripts record the directory they were created in.
  # Rewrite those paths to the final prefix before the directory is renamed.
  local root="$1" old="$2" new="$3" file line rewritten tmp
  while IFS= read -r -d '' file; do
    [[ -L "$file" ]] && continue
    IFS= read -r line < "$file" || continue
    [[ "$line" == "#!${old}/"* ]] || continue
    rewritten="${line/"#!${old}/"/"#!${new}/"}"
    tmp="$(mktemp)" || return 1
    {
      printf '%s\n' "$rewritten"
      tail -n +2 "$file"
    } > "$tmp" || return 1
    cat "$tmp" > "$file" || return 1
    rm -f "$tmp"
    chmod +x "$file" || return 1
  done < <(find "$root/bin" -type f -print0)
  if [[ -f "${root}/pyvenv.cfg" ]]; then
    sed -i "s#${old}#${new}#g" "${root}/pyvenv.cfg" || return 1
  fi
  if [[ -f "${root}/bin/activate" ]]; then
    sed -i "s#${old}#${new}#g" "${root}/bin/activate" || return 1
  fi
}

smoke_version() {
  local bin="$1" expected="$2" line ver
  line="$("$bin" --version 2>&1)" || return 1
  ver="${line#meteorcli }"
  ver="${ver//$'\r'/}"
  [[ "$ver" == "$expected" ]]
}

smoke_runs() {
  local bin="$1" line
  line="$("$bin" --version 2>&1)" || return 1
  [[ "$line" == meteorcli\ * ]]
}

current_version() {
  local bin="${BIN_DIR}/meteorcli" line ver
  [[ -x "$bin" ]] || return 0
  line="$("$bin" --version 2>/dev/null || true)"
  ver="${line#meteorcli }"
  ver="${ver//$'\r'/}"
  if valid_version "$ver"; then
    printf '%s\n' "$ver"
  else
    echo "warning: could not parse installed version (${line})" >&2
  fi
}

config_dir() {
  if [[ -n "${METEORCLI_CONFIG_DIR:-}" ]]; then
    printf '%s\n' "$METEORCLI_CONFIG_DIR"
    return 0
  fi
  if [[ "${EUID}" -eq 0 && "$PREFIX" == "/opt/meteorcli" ]]; then
    printf '%s\n' /etc/meteorcli
    return 0
  fi
  printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/meteorcli"
}

config_fingerprint() {
  local dir="$1"
  if [[ ! -e "$dir" ]]; then
    echo "absent"
    return 0
  fi
  [[ -d "$dir" ]] || die "config path ${dir} is not a directory"
  (
    cd "$dir"
    find . -type f -print | LC_ALL=C sort | while IFS= read -r file; do
      sha256sum "$file"
    done
  )
}

systemd_in_charge() {
  command -v systemctl >/dev/null 2>&1 || return 1
  if [[ "${METEORCLI_UPDATE_ASSUME_SYSTEMD:-}" != 1 ]]; then
    [[ -d /run/systemd/system ]] || return 1
  fi
  systemctl cat meteorcli.service >/dev/null 2>&1
}

note_service() {
  WAS_ACTIVE=0
  if systemd_in_charge && systemctl is-active --quiet meteorcli; then
    WAS_ACTIVE=1
  fi
}

stop_if_active() {
  [[ "$WAS_ACTIVE" -eq 1 ]] || return 0
  systemctl stop meteorcli || return 1
  STOPPED=1
}

start_if_needed() {
  [[ "$WAS_ACTIVE" -eq 1 ]] || return 0
  systemctl daemon-reload || return 1
  systemctl start meteorcli || return 1
}

service_is_healthy() {
  local attempt pid start_restarts end_restarts hold tries
  hold="${METEORCLI_UPDATE_HEALTH_SECONDS:-15}"
  [[ "$hold" =~ ^[0-9]+$ ]] || hold=15
  tries=15
  if [[ "$hold" == "0" ]]; then
    tries=1
  fi
  for ((attempt = 0; attempt < tries; attempt++)); do
    if systemctl is-active --quiet meteorcli; then
      break
    fi
    if [[ "$hold" != "0" ]]; then
      sleep 1
    fi
  done
  systemctl is-active --quiet meteorcli || return 1
  pid="$(systemctl show -p MainPID --value meteorcli.service)" || return 1
  [[ -n "$pid" && "$pid" != "0" && -d "/proc/${pid}" ]] || return 1
  start_restarts="$(systemctl show -p NRestarts --value meteorcli.service)" || return 1
  if [[ "$hold" != "0" ]]; then
    sleep "$hold" || return 1
  fi
  systemctl is-active --quiet meteorcli || return 1
  end_restarts="$(systemctl show -p NRestarts --value meteorcli.service)" || return 1
  [[ "$start_restarts" == "$end_restarts" ]]
}

snapshot_helpers() {
  local dest="$1" name
  [[ "${EUID}" -eq 0 ]] || return 0
  rm -rf "$dest" || return 1
  mkdir -p "$dest" || return 1
  for name in "${HELPER_NAMES[@]}"; do
    if [[ -f "/usr/local/libexec/${name}" ]]; then
      cp -a "/usr/local/libexec/${name}" "${dest}/${name}" || return 1
    fi
  done
  if [[ -f /etc/systemd/system/meteorcli.service ]]; then
    cp -a /etc/systemd/system/meteorcli.service "${dest}/meteorcli.service" || return 1
  fi
}

restore_helpers_from() {
  local src="$1" name
  [[ "${EUID}" -eq 0 ]] || return 0
  [[ -d "$src" ]] || return 0
  for name in "${HELPER_NAMES[@]}"; do
    if [[ -f "${src}/${name}" ]]; then
      mkdir -p /usr/local/libexec
      cp -a "${src}/${name}" "/usr/local/libexec/${name}"
    fi
  done
  if [[ -f "${src}/meteorcli.service" ]]; then
    cp -a "${src}/meteorcli.service" /etc/systemd/system/meteorcli.service
    systemctl daemon-reload || true
  fi
}

cleanup_staged() {
  if [[ -n "${PREFIX:-}" && -d "${PREFIX}.next" ]]; then
    rm -rf "${PREFIX}.next"
  fi
}

restore_previous() {
  if [[ -d "$PREFIX" && "$SWAPPED" -eq 1 ]]; then
    rm -rf "${PREFIX}.failed"
    mv "$PREFIX" "${PREFIX}.failed"
  fi
  if [[ -d "${PREFIX}.previous" ]]; then
    [[ ! -d "$PREFIX" ]] || return 1
    mv "${PREFIX}.previous" "$PREFIX"
  fi
  if [[ -x "${PREFIX}/bin/meteorcli" ]]; then
    mkdir -p "$BIN_DIR"
    ln -sfn "${PREFIX}/bin/meteorcli" "${BIN_DIR}/meteorcli"
  fi
  restore_helpers_from "${STATE_DIR}/previous-helpers" || true
  if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    systemctl daemon-reload || true
    systemctl start meteorcli || return 1
  fi
  rm -rf "${PREFIX}.failed"
}

fail() {
  trap - ERR
  set +e
  echo "error: $*" >&2
  cleanup_staged
  if [[ "$SWAPPED" -eq 1 || "$MOVED_ASIDE" -eq 1 ]]; then
    echo "meteorcli-update: restoring the previous version" >&2
    restore_previous || echo "error: automatic restore failed; look at ${PREFIX} and ${PREFIX}.previous" >&2
  elif [[ "$STOPPED" -eq 1 ]]; then
    systemctl start meteorcli || echo "error: could not start meteorcli.service again" >&2
  fi
  exit 1
}

swap_in() {
  # Called as `swap_in || fail`, so a failed mv must return rather than fall through.
  mkdir -p "$BIN_DIR" || return 1
  if [[ -d "$PREFIX" ]]; then
    rm -rf "${PREFIX}.previous" || return 1
    mv "$PREFIX" "${PREFIX}.previous" || return 1
    MOVED_ASIDE=1
  fi
  mv "${PREFIX}.next" "$PREFIX" || return 1
  SWAPPED=1
  ln -sfn "${PREFIX}/bin/meteorcli" "${BIN_DIR}/meteorcli"
}

refresh_helpers() {
  [[ "${EUID}" -eq 0 ]] || return 0
  [[ -f /etc/systemd/system/meteorcli.service ]] || return 0
  if ! grep -q -- '--helpers-only' "$INSTALLER"; then
    echo "note: this release does not refresh root helpers; left them unchanged"
    return 0
  fi
  bash "$INSTALLER" --helpers-only --prefix "$PREFIX" --bin-dir "$BIN_DIR" || return 1
}

install_updater_from_release() {
  local src dest
  src="${AGENT_DIR}/packaging/update-meteorcli.sh"
  [[ -f "$src" ]] || return 0
  if [[ "${EUID}" -eq 0 ]]; then
    dest=/usr/local/libexec/meteorcli-update
  else
    dest="${HOME}/.local/libexec/meteorcli-update"
  fi
  mkdir -p "$(dirname "$dest")" || return 1
  cp "$src" "$dest" || return 1
  chmod 0755 "$dest" || return 1
}

record_status() {
  mkdir -p "$STATE_DIR" || return 1
  cat > "${STATE_DIR}/status" <<EOF || return 1
current=${TARGET}
previous=${CURRENT_VERSION}
prefix=${PREFIX}
bin=${BIN_DIR}
EOF
}

confirm() {
  local prompt="$1" answer
  if [[ "$YES" -eq 1 ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    echo "error: not a terminal; pass --yes to continue" >&2
    exit 1
  fi
  printf '%s [y/N] ' "$prompt"
  read -r answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || die "aborted"
}

decide() {
  local current="$1" target="$2" explicit="$3"
  if [[ -n "$current" && "$current" == "$target" ]]; then
    echo "meteorcli ${current} is already installed"
    exit 0
  fi
  if [[ -n "$current" ]] && ! version_is_newer "$target" "$current"; then
    if [[ "$explicit" -eq 1 && "$ALLOW_DOWNGRADE" -eq 1 ]]; then
      echo "warning: downgrade ${current} -> ${target}" >&2
      DOWNGRADING=1
    elif [[ "$explicit" -eq 1 ]]; then
      die "refusing to replace ${current} with older ${target}; pass --allow-downgrade to do that on purpose"
    else
      echo "installed meteorcli ${current} is newer than published ${target}"
      exit 0
    fi
  fi
  if [[ "$CHECK" -eq 1 ]]; then
    if [[ -n "$current" ]]; then
      echo "update available: ${current} -> ${target}"
    else
      echo "meteorcli ${target} is published; ${BIN_DIR}/meteorcli is not installed"
    fi
    exit 10
  fi
}

undo_rollback() {
  systemctl stop meteorcli >/dev/null 2>&1 || true
  if [[ -d "$PREFIX" ]]; then
    rm -rf "${PREFIX}.previous"
    mv "$PREFIX" "${PREFIX}.previous" || true
  fi
  if [[ -d "${PREFIX}.held" ]]; then
    mv "${PREFIX}.held" "$PREFIX" || true
  fi
  if [[ -x "${PREFIX}/bin/meteorcli" ]]; then
    ln -sfn "${PREFIX}/bin/meteorcli" "${BIN_DIR}/meteorcli"
  fi
  restore_helpers_from "${STATE_DIR}/current-helpers" || true
  start_if_needed || true
}

do_rollback() {
  local before after
  [[ -d "${PREFIX}.previous" ]] || die "no previous install at ${PREFIX}.previous"
  [[ -x "${PREFIX}.previous/bin/meteorcli" ]] || die "previous install has no meteorcli binary"
  note_service
  echo "Restore the install at ${PREFIX}.previous."
  echo "Device config is not modified. If the restored service does not stay active, the other install is put back."
  confirm "Roll back meteorcli ${CURRENT_VERSION:-unknown}?"
  before="$(config_fingerprint "$(config_dir)")"
  snapshot_helpers "${STATE_DIR}/current-helpers"
  stop_if_active || die "could not stop meteorcli.service; left the current install in place"
  rm -rf "${PREFIX}.held"
  mv "$PREFIX" "${PREFIX}.held"
  if ! mv "${PREFIX}.previous" "$PREFIX"; then
    mv "${PREFIX}.held" "$PREFIX" || true
    start_if_needed || true
    die "could not move the previous install back"
  fi
  ln -sfn "${PREFIX}/bin/meteorcli" "${BIN_DIR}/meteorcli"
  restore_helpers_from "${STATE_DIR}/previous-helpers" || true
  if ! smoke_runs "${PREFIX}/bin/meteorcli"; then
    undo_rollback
    die "previous meteorcli did not run; put the other install back"
  fi
  if ! start_if_needed; then
    undo_rollback
    die "could not start meteorcli.service; put the other install back"
  fi
  if [[ "$WAS_ACTIVE" -eq 1 ]] && ! service_is_healthy; then
    undo_rollback
    die "rolled-back meteorcli.service did not stay active; put the other install back"
  fi
  after="$(config_fingerprint "$(config_dir)")"
  if [[ "$before" != "$after" ]]; then
    undo_rollback
    die "device config changed during rollback; put the other install back"
  fi
  rm -rf "${PREFIX}.previous"
  mv "${PREFIX}.held" "${PREFIX}.previous"
  echo "rolled back meteorcli; the other install is at ${PREFIX}.previous"
}

apply_update() {
  local before after
  ensure_free_space
  download_release
  decide "$CURRENT_VERSION" "$TARGET" "$EXPLICIT"
  note_service
  echo "meteorcli ${CURRENT_VERSION:-not installed} -> ${TARGET}"
  echo "install: ${PREFIX}"
  echo "command: ${BIN_DIR}/meteorcli"
  if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    echo "meteorcli.service stops only for the switch, then starts again."
  fi
  echo "Device config is not modified. If the new version does not stay up, the previous install is restored."
  if [[ "$DOWNGRADING" -eq 1 ]]; then
    confirm "Downgrade meteorcli ${CURRENT_VERSION} to ${TARGET}?"
  else
    confirm "Update meteorcli ${CURRENT_VERSION:-not installed} to ${TARGET}?"
  fi
  before="$(config_fingerprint "$(config_dir)")" || fail "could not read device config"
  stage_venv "$AGENT_DIR" "${PREFIX}.next" || fail "could not install meteorcli ${TARGET}"
  smoke_version "${PREFIX}.next/bin/meteorcli" "$TARGET" || fail "staged meteorcli did not report ${TARGET}"
  snapshot_helpers "${STATE_DIR}/previous-helpers" || fail "could not save the current root helpers"
  stop_if_active || fail "could not stop meteorcli.service; left the current install in place"
  fix_venv_paths "${PREFIX}.next" "${PREFIX}.next" "$PREFIX" || fail "could not prepare the new install"
  swap_in || fail "could not switch to the new install"
  smoke_version "${PREFIX}/bin/meteorcli" "$TARGET" || fail "installed meteorcli did not report ${TARGET}"
  refresh_helpers || fail "could not refresh root helpers"
  start_if_needed || fail "could not start meteorcli.service"
  if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    service_is_healthy || fail "meteorcli.service did not stay active"
  fi
  after="$(config_fingerprint "$(config_dir)")" || fail "could not read device config after the update"
  [[ "$before" == "$after" ]] || fail "device config changed during the update"
  install_updater_from_release || echo "warning: could not install the meteorcli-update command" >&2
  record_status || echo "warning: could not record update status" >&2
  echo "updated meteorcli ${CURRENT_VERSION:-not installed} -> ${TARGET}"
  if [[ -d "${PREFIX}.previous" ]]; then
    echo "previous install kept at ${PREFIX}.previous"
    echo "restore it with: meteorcli update --rollback --yes"
  fi
}

acquire_lock() {
  local lock="${METEORCLI_UPDATE_LOCK:-${TMPDIR:-/tmp}/meteorcli-update.lock}"
  command -v flock >/dev/null 2>&1 || die "flock is required (util-linux)"
  mkdir -p "$(dirname "$lock")"
  exec 9>"$lock"
  flock -n 9 || die "another meteorcli update is already running"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --check)
        CHECK=1
        shift
        ;;
      --yes)
        YES=1
        shift
        ;;
      --to)
        TO="${2:?--to requires a version}"
        shift 2
        ;;
      --allow-downgrade)
        ALLOW_DOWNGRADE=1
        shift
        ;;
      --rollback)
        ROLLBACK=1
        shift
        ;;
      --prefix)
        PREFIX="${2:?--prefix requires a directory}"
        shift 2
        ;;
      --bin-dir)
        BIN_DIR="${2:?--bin-dir requires a directory}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done
}

resolve_layout() {
  if [[ -z "$PREFIX" ]]; then
    if [[ "${EUID}" -eq 0 ]]; then
      PREFIX="/opt/meteorcli"
    else
      PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/meteorcli"
    fi
  fi
  if [[ -z "$BIN_DIR" ]]; then
    if [[ "${EUID}" -eq 0 ]]; then
      BIN_DIR="/usr/local/bin"
    else
      BIN_DIR="${HOME}/.local/bin"
    fi
  fi
  if [[ -n "${METEORCLI_UPDATE_STATE_DIR:-}" ]]; then
    STATE_DIR="$METEORCLI_UPDATE_STATE_DIR"
  elif [[ "${EUID}" -eq 0 ]]; then
    STATE_DIR="/var/lib/meteorcli-update"
  else
    STATE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/meteorcli-update"
  fi
  case "$PREFIX" in
    /|/usr|/usr/local|/opt|/home|/root) die "refusing to use prefix ${PREFIX}" ;;
  esac
  [[ "$PREFIX" == *meteorcli* ]] || die "prefix ${PREFIX} must contain meteorcli"
  case "$BIN_DIR" in
    /|/usr|/bin|/usr/bin) die "refusing bin dir ${BIN_DIR}" ;;
  esac
}

resolve_target() {
  local env_version="${METEORCLI_VERSION:-}"
  if [[ -n "$TO" ]]; then
    valid_version "$TO" || die "--to must be X.Y.Z (got ${TO})"
    TARGET="$TO"
    EXPLICIT=1
    return 0
  fi
  if [[ -n "$env_version" && "$env_version" != "latest" ]]; then
    env_version="${env_version#agent-v}"
    env_version="${env_version#v}"
    valid_version "$env_version" || die "METEORCLI_VERSION must be X.Y.Z or agent-vX.Y.Z"
    TARGET="$env_version"
    EXPLICIT=1
  fi
}

cleanup() {
  if [[ -n "${WORKDIR:-}" ]]; then
    rm -rf "$WORKDIR"
  fi
  if [[ "${METEORCLI_UPDATE_INNER:-}" == 1 ]]; then
    rm -f -- "$0"
  fi
}

main() {
  parse_args "$@"
  [[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "invalid METEORCLI_RELEASE_REPO"
  if [[ -n "$DIST_URL" && "$DIST_URL" != https://* && "$DIST_URL" != http://* ]]; then
    die "METEORCLI_DIST_URL must be an http(s) URL"
  fi
  if [[ "$ROLLBACK" -eq 1 && "$CHECK" -eq 1 ]]; then
    die "--rollback and --check cannot be used together"
  fi
  acquire_lock
  resolve_layout
  PYTHON="$(find_python)" || die "Python 3.11+ is required"
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/meteorcli-update.XXXXXX")"
  trap cleanup EXIT
  CURRENT_VERSION="$(current_version)"
  if [[ "$ROLLBACK" -eq 1 ]]; then
    do_rollback
    return 0
  fi
  resolve_target
  if [[ -z "$TARGET" && -z "$DIST_URL" ]]; then
    TARGET="$(fetch_latest_version)" || die "could not read an agent-vX.Y.Z release from ${REPO}"
    valid_version "$TARGET" || die "published version ${TARGET} is not X.Y.Z"
  fi
  if [[ -n "$TARGET" && -z "$DIST_URL" ]]; then
    # Refuse a downgrade or report --check before downloading.
    # apply_update compares again once the tarball's own version is known.
    decide "$CURRENT_VERSION" "$TARGET" "$EXPLICIT"
  fi
  if [[ "$CHECK" -eq 1 && -z "$DIST_URL" ]]; then
    return 0
  fi
  apply_update
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  # Copy out of the install tree before replacing it. bash reads the script
  # file as it runs; overwriting that file mid-update would execute a mix of
  # old and new bytes. A pipe (curl | bash) is not a file we replace.
  if [[ -z "${METEORCLI_UPDATE_INNER:-}" && -f "$0" && "$(basename "$0")" != bash ]]; then
    copy="$(mktemp "${TMPDIR:-/tmp}/meteorcli-update.XXXXXX")"
    cp "$0" "$copy"
    chmod 0700 "$copy"
    export METEORCLI_UPDATE_INNER=1
    exec bash "$copy" "$@"
  fi
  main "$@"
fi

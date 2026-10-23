#!/usr/bin/env bash
# Install MeteorCloud on this VM / existing server.
# Host packages via Ansible (Ubuntu/Debian or RHEL/CentOS/Rocky/Alma 9+);
# the app itself runs in Docker.
# Clone the repo on the host, then from the repo root:
#
#   make up PLATFORM_DOMAIN=fleet.example.com
#   make up ARGS='--no-vpn --mqtt=off --storage=filesystem'
#   make down
#   make down ARGS=--destroy-data
#
# On a terminal, make up shows a checkbox of optional services (all on by
# default: WireGuard, MQTT, MinIO) and prints DNS records for that setup.
# Non-interactive: METEORCLOUD_NONINTERACTIVE=1 plus PLATFORM_DOMAIN and flags.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
INV="${HERE}/inventory/selfhost"
SECRETS="${INV}/secrets.env"
HOME_DIR="${HOME}/.meteorcloud"
HOME_SECRETS="${HOME_DIR}/secrets.env"
INSTALL_ENV="${HOME_DIR}/install.env"
ARCHIVE="${INV}/source.tar.gz"
VENV="${HOME}/.meteorcloud-ansible"
VPN=1
MQTT=bundled
STORAGE=minio
DOWN=0
DESTROY_DATA=0
ASK_VPN=1
ASK_MQTT=1
ASK_STORAGE=1

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  echo "Options:"
  echo "  --no-vpn | --vpn"
  echo "  --mqtt=off|bundled|external"
  echo "  --storage=filesystem|s3|minio"
  echo "  --down | --destroy-data"
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-vpn) VPN=0; ASK_VPN=0 ;;
    --vpn) VPN=1; ASK_VPN=0 ;;
    --mqtt=off|--mqtt=bundled|--mqtt=external)
      MQTT="${1#--mqtt=}"
      ASK_MQTT=0
      ;;
    --storage=filesystem|--storage=s3|--storage=minio)
      STORAGE="${1#--storage=}"
      ASK_STORAGE=0
      ;;
    --down) DOWN=1 ;;
    --destroy-data) DOWN=1; DESTROY_DATA=1 ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
  shift
done

if [[ -d "${HERE}/inventory/local" && ! -d "${INV}" ]]; then
  mv "${HERE}/inventory/local" "${INV}"
fi

if ! sudo -n true 2>/dev/null; then
  echo "Need sudo for Docker, packages, and /opt/edge-platform. Enter your password if asked."
  sudo -v
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Need /etc/os-release to detect the host OS." >&2
  exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
_id_like="${ID_LIKE:-} ${ID:-}"
if [[ "${_id_like}" == *debian* || "${_id_like}" == *ubuntu* ]]; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates curl git make openssl python3 python3-venv python3-full >/dev/null
elif [[ "${_id_like}" == *rhel* || "${_id_like}" == *centos* ]]; then
  _pkg=dnf
  command -v dnf >/dev/null 2>&1 || _pkg=yum
  sudo "${_pkg}" install -y -q ca-certificates curl git make openssl python3 python3-pip
else
  echo "Need Ubuntu/Debian or RHEL/CentOS/Rocky/AlmaLinux 9+ (found ${ID:-unknown})." >&2
  exit 1
fi

ansible_bin() {
  if command -v ansible-playbook >/dev/null 2>&1; then
    command -v ansible-playbook
    return
  fi
  if [[ -x "${VENV}/bin/ansible-playbook" ]]; then
    echo "${VENV}/bin/ansible-playbook"
    return
  fi
  echo "Installing Ansible into ${VENV}" >&2
  python3 -m venv "${VENV}"
  "${VENV}/bin/pip" install -q 'ansible>=9'
  echo "${VENV}/bin/ansible-playbook"
}

ensure_galaxy() {
  local playbook_bin="$1"
  local galaxy
  galaxy="$(dirname "${playbook_bin}")/ansible-galaxy"
  if [[ -x "${galaxy}" ]]; then
    "${galaxy}" collection install -r "${HERE}/requirements.yml" >/dev/null
  fi
  export PATH="$(dirname "${playbook_bin}"):${PATH}"
}

play_secrets_args() {
  local args=() f
  for f in "${SECRETS}" "${HOME_SECRETS}"; do
    if [[ -f "${f}" ]]; then
      # shellcheck disable=SC1090
      set -a && . "${f}" && set +a
      break
    fi
  done
  if [[ -n "${EDGE_PLATFORM_POSTGRES_PASSWORD:-}" ]]; then
    args+=(-e "postgres_password=${EDGE_PLATFORM_POSTGRES_PASSWORD}")
  fi
  if [[ -n "${EDGE_PLATFORM_JWT_SECRET:-}" ]]; then
    args+=(-e "jwt_secret=${EDGE_PLATFORM_JWT_SECRET}")
  fi
  if [[ -n "${EDGE_PLATFORM_ADMIN_EMAIL:-}" ]]; then
    args+=(-e "admin_email=${EDGE_PLATFORM_ADMIN_EMAIL}")
  fi
  if [[ -n "${EDGE_PLATFORM_ADMIN_PASSWORD:-}" ]]; then
    args+=(-e "admin_password=${EDGE_PLATFORM_ADMIN_PASSWORD}")
  fi
  if [[ -n "${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-}" ]]; then
    args+=(-e "connectivity_internal_token=${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}")
    args+=(-e '{"enabled_services":["cloud_app","vpn"]}')
  else
    args+=(-e '{"enabled_services":["cloud_app"]}')
  fi
  printf '%s\n' "${args[@]}"
}

if [[ "${DOWN}" -eq 1 ]]; then
  if [[ ! -f "${INV}/hosts.yml" ]]; then
    echo "No install on this host (missing ${INV}/hosts.yml). Run make up first." >&2
    exit 1
  fi
  ANSIBLE_BIN="$(ansible_bin)"
  ensure_galaxy "${ANSIBLE_BIN}"
  echo "Stopping MeteorCloud on this VM"
  cd "${HERE}"
  mapfile -t SECRET_ARGS < <(play_secrets_args)
  "${ANSIBLE_BIN}" -i "${INV}/hosts.yml" playbooks/destroy.yml \
    "${SECRET_ARGS[@]}" \
    -e "platform_destroy_data=${DESTROY_DATA}"
  echo
  if [[ "${DESTROY_DATA}" -eq 1 ]]; then
    echo "MeteorCloud and its data under /opt/edge-platform are gone."
  else
    echo "MeteorCloud is stopped. Data is in /opt/edge-platform. Re-run make up to start it again."
  fi
  exit 0
fi

mkdir -p "${INV}/group_vars" "${HOME_DIR}"
chmod 700 "${INV}" "${HOME_DIR}"

if [[ -f "${INSTALL_ENV}" ]]; then
  # shellcheck disable=SC1090
  set -a && . "${INSTALL_ENV}" && set +a
fi

is_tty() { [[ -t 0 && -t 1 && -z "${METEORCLOUD_NONINTERACTIVE:-}" ]]; }

ask_yn() {
  local prompt="$1" def="${2:-Y}" reply
  if ! is_tty; then
    [[ "${def}" == [Yy] ]]
    return
  fi
  if [[ "${def}" == [Yy] ]]; then
    read -r -p "${prompt} [Y/n] " reply || true
    [[ -z "${reply}" || "${reply}" == [Yy]* ]]
  else
    read -r -p "${prompt} [y/N] " reply || true
    [[ "${reply}" == [Yy]* ]]
  fi
}

ask_line() {
  local prompt="$1" def="${2:-}" var
  if ! is_tty; then
    printf '%s\n' "${def}"
    return
  fi
  if [[ -n "${def}" ]]; then
    read -r -p "${prompt} [${def}]: " var || true
    printf '%s\n' "${var:-${def}}"
  else
    read -r -p "${prompt}: " var || true
    printf '%s\n' "${var}"
  fi
}

ask_secret() {
  local prompt="$1" var
  if ! is_tty; then
    printf '%s\n' ""
    return
  fi
  read -r -s -p "${prompt}: " var || true
  echo >&2
  printf '%s\n' "${var}"
}

detect_public_ip() {
  local ip=""
  ip="$(curl -fsS --max-time 2 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  if [[ -z "${ip}" ]]; then
    ip="$(curl -fsS --max-time 2 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  printf '%s\n' "${ip}"
}

normalize_domain() {
  local d="${1:-}"
  d="${d#http://}"
  d="${d#https://}"
  d="${d%%/*}"
  d="${d%.}"
  printf '%s\n' "${d}"
}

print_dns_guide() {
  local domain="$1" ip="$2"
  echo
  echo "Create these DNS A records (DNS only, no HTTP proxy, TTL 60-300s)"
  echo "pointing at ${ip:-<this server public IP>}:"
  echo
  printf '  %-22s  %-28s  %s\n' "Service" "Name" "Purpose"
  printf '  %-22s  %-28s  %s\n' "cloud_app" "${domain}" "console + API (TCP 80, 443)"
  printf '  %-22s  %-28s  %s\n' "cloud_app (apps)" "*.${domain}" "public app URLs (TCP 443)"
  if [[ "${VPN}" -eq 1 ]]; then
    printf '  %-22s  %-28s  %s\n' "vpn" "vpn.${domain}" "WireGuard (UDP 51820)"
  fi
  if [[ "${MQTT}" == bundled ]]; then
    printf '  %-22s  %-28s  %s\n' "mqtt" "mqtt.${domain}" "device MQTT TLS (TCP 8883)"
  fi
  echo
  if [[ "${MQTT}" == external ]]; then
    echo "  mqtt       uses your broker (${MQTT_BROKER_HOST:-set MQTT_BROKER_HOST}); no record on this server"
  elif [[ "${MQTT}" == off ]]; then
    echo "  mqtt       not enabled"
  fi
  case "${STORAGE}" in
    filesystem)
      echo "  storage    this disk; downloads go through https://${domain}/api (no public hostname)"
      ;;
    minio)
      echo "  storage    bundled MinIO on this host; not public. Downloads go through https://${domain}/api"
      ;;
    s3)
      echo "  storage    your S3 bucket; no DNS on this server. Downloads go through https://${domain}/api"
      ;;
  esac
  echo
  echo "Details: docs/dns.md"
}

if [[ "${ASK_VPN}" -eq 1 && -n "${ENABLE_VPN:-}" ]]; then VPN="${ENABLE_VPN}"; fi
if [[ "${ASK_MQTT}" -eq 1 && -n "${MQTT_MODE:-}" ]]; then MQTT="${MQTT_MODE}"; fi
if [[ "${ASK_STORAGE}" -eq 1 && -n "${STORAGE_MODE:-}" ]]; then STORAGE="${STORAGE_MODE}"; fi
DOMAIN_RAW="${PLATFORM_DOMAIN:-${DOMAIN:-}}"
DOMAIN="$(normalize_domain "${DOMAIN_RAW}")"

PREFERRED_MQTT="${MQTT}"
[[ "${PREFERRED_MQTT}" == off ]] && PREFERRED_MQTT=bundled
PREFERRED_STORAGE="${STORAGE}"
[[ "${PREFERRED_STORAGE}" == filesystem ]] && PREFERRED_STORAGE=minio

mark() { if [[ "$1" -eq 1 ]]; then printf 'x'; else printf ' '; fi; }

select_services() {
  local reply mqtt_on storage_on mqtt_where storage_where
  echo
  echo "Optional services — all on. Type a number to toggle, Enter to continue."
  while true; do
    mqtt_on=0
    storage_on=0
    [[ "${MQTT}" != off ]] && mqtt_on=1
    [[ "${STORAGE}" != filesystem ]] && storage_on=1
    mqtt_where="Bundled EMQX on this host."
    [[ "${MQTT}" == external ]] && mqtt_where="Your existing broker."
    storage_where="Bundled MinIO on this host. Off = files on this disk."
    [[ "${STORAGE}" == s3 ]] && storage_where="Your S3 bucket. Off = files on this disk."
    echo
    printf '  [%s] 1  WireGuard (vpn)\n' "$(mark "${VPN}")"
    echo "         What: a private tunnel devices dial."
    echo "         For:  browser SSH and app endpoints, without public SSH."
    echo
    printf '  [%s] 2  MQTT (data plane)\n' "$(mark "${mqtt_on}")"
    echo "         What: a TLS broker for device messages."
    echo "         For:  live telemetry and commands. Enroll/HTTP work without it."
    echo "         ${mqtt_where}"
    echo
    printf '  [%s] 3  Artifact storage\n' "$(mark "${storage_on}")"
    echo "         What: where OS images, firmware, and compose bundles live."
    echo "         For:  operators upload in the console; devices download via the API."
    echo "         ${storage_where}"
    echo
    read -r -p "> " reply || true
    case "${reply}" in
      "") break ;;
      1) if [[ "${VPN}" -eq 1 ]]; then VPN=0; else VPN=1; fi ;;
      2) if [[ "${MQTT}" == off ]]; then MQTT="${PREFERRED_MQTT}"; else MQTT=off; fi ;;
      3) if [[ "${STORAGE}" == filesystem ]]; then STORAGE="${PREFERRED_STORAGE}"; else STORAGE=filesystem; fi ;;
      *) echo "Type 1, 2 or 3 to toggle, or Enter to continue." ;;
    esac
  done
}

if is_tty; then
  echo
  echo "MeteorCloud production install on this VM"
  echo "The console and API will be https://<your-domain> after DNS is in place."
  echo
  if [[ -z "${DOMAIN}" ]]; then
    DOMAIN="$(normalize_domain "$(ask_line "Domain for this install (e.g. fleet.example.com)")")"
  fi
  select_services
fi

if [[ -z "${DOMAIN}" || "${DOMAIN}" != *.* || "${DOMAIN}" == *[^-A-Za-z0-9.]* ]]; then
  echo "Production install needs your domain. Example:" >&2
  echo "  make up PLATFORM_DOMAIN=fleet.example.com" >&2
  print_dns_guide "fleet.example.com" "<server public IP>" >&2
  exit 1
fi

load_existing_secrets() {
  local f
  for f in "${SECRETS}" "${HOME_SECRETS}"; do
    if [[ -f "${f}" ]]; then
      # shellcheck disable=SC1090
      set -a && . "${f}" && set +a
      return 0
    fi
  done
  return 1
}

SECRETS_REUSED=0
if load_existing_secrets; then
  SECRETS_REUSED=1
fi

PUBLIC_IP="$(detect_public_ip)"
PUBLIC_URL="${PLATFORM_PUBLIC_URL:-https://${DOMAIN}}"
VPN_HOST="vpn.${DOMAIN}"
MQTT_HOST="mqtt.${DOMAIN}"
MQTT_BROKER_HOST="${MQTT_BROKER_HOST:-}"
MQTT_BROKER_PORT="${MQTT_BROKER_PORT:-8883}"
OBJECT_STORAGE_BUCKET="${OBJECT_STORAGE_BUCKET:-meteorcloud-artifacts}"
OBJECT_STORAGE_REGION="${OBJECT_STORAGE_REGION:-us-east-1}"
OBJECT_STORAGE_ENDPOINT_URL="${OBJECT_STORAGE_ENDPOINT_URL:-}"

if [[ "${STORAGE}" == s3 ]]; then
  echo
  echo "S3 / S3-compatible settings (leave access keys empty to use an instance role)."
  OBJECT_STORAGE_BUCKET="$(ask_line "Bucket" "${OBJECT_STORAGE_BUCKET}")"
  OBJECT_STORAGE_REGION="$(ask_line "Region" "${OBJECT_STORAGE_REGION}")"
  OBJECT_STORAGE_ENDPOINT_URL="$(ask_line "Endpoint URL (empty for AWS S3)" "${OBJECT_STORAGE_ENDPOINT_URL}")"
  if [[ -z "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID:-}" ]]; then
    EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID="$(ask_line "Access key id (empty if instance role)" "")"
  fi
  if [[ -n "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID}" && -z "${EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY:-}" ]]; then
    EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY="$(ask_secret "Secret access key")"
  fi
  if [[ -z "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID}" && -z "${OBJECT_STORAGE_ENDPOINT_URL}" ]]; then
    :
  elif [[ -z "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID}" || -z "${EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY:-}" ]]; then
    if [[ -n "${OBJECT_STORAGE_ENDPOINT_URL}" ]]; then
      echo "S3-compatible endpoints need access key and secret." >&2
      exit 1
    fi
  fi
fi

if [[ "${MQTT}" == external ]]; then
  echo
  echo "External MQTT broker"
  MQTT_BROKER_HOST="$(ask_line "Broker hostname" "${MQTT_BROKER_HOST}")"
  MQTT_BROKER_PORT="$(ask_line "Broker TLS port" "${MQTT_BROKER_PORT}")"
  if [[ -z "${MQTT_BROKER_HOST}" ]]; then
    echo "An external broker needs a hostname." >&2
    exit 1
  fi
fi

print_dns_guide "${DOMAIN}" "${PUBLIC_IP:-<this server public IP>}"
if is_tty; then
  echo "Create those records, then continue. HTTPS works after DNS reaches this host."
  ask_yn "Continue installation" Y || exit 1
fi

{
  printf 'PLATFORM_DOMAIN=%s\n' "${DOMAIN}"
  printf 'ENABLE_VPN=%s\n' "${VPN}"
  printf 'STORAGE_MODE=%s\n' "${STORAGE}"
  printf 'MQTT_MODE=%s\n' "${MQTT}"
  printf 'MQTT_BROKER_HOST=%s\n' "${MQTT_BROKER_HOST}"
  printf 'MQTT_BROKER_PORT=%s\n' "${MQTT_BROKER_PORT}"
  printf 'OBJECT_STORAGE_BUCKET=%s\n' "${OBJECT_STORAGE_BUCKET}"
  printf 'OBJECT_STORAGE_REGION=%s\n' "${OBJECT_STORAGE_REGION}"
  printf 'OBJECT_STORAGE_ENDPOINT_URL=%s\n' "${OBJECT_STORAGE_ENDPOINT_URL}"
} > "${INSTALL_ENV}"
chmod 600 "${INSTALL_ENV}"

EXTRA_VARS='{"enabled_services":["cloud_app"]}'
if [[ "${VPN}" -eq 1 ]]; then
  EXTRA_VARS='{"enabled_services":["cloud_app","vpn"]}'
fi

rand() { openssl rand -hex "$1"; }

export EDGE_PLATFORM_POSTGRES_PASSWORD="${EDGE_PLATFORM_POSTGRES_PASSWORD:-$(rand 16)}"
export EDGE_PLATFORM_JWT_SECRET="${EDGE_PLATFORM_JWT_SECRET:-$(rand 32)}"
export EDGE_PLATFORM_ADMIN_EMAIL="${EDGE_PLATFORM_ADMIN_EMAIL:-admin@${DOMAIN}}"
export EDGE_PLATFORM_ACME_EMAIL="${EDGE_PLATFORM_ACME_EMAIL:-${EDGE_PLATFORM_ADMIN_EMAIL}}"
export EDGE_PLATFORM_ADMIN_PASSWORD="${EDGE_PLATFORM_ADMIN_PASSWORD:-$(rand 12)}"
if [[ "${VPN}" -eq 1 ]]; then
  export EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN="${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-$(rand 24)}"
fi
if [[ "${MQTT}" != off ]]; then
  export EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD="${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD:-$(rand 16)}"
  export EDGE_PLATFORM_MQTT_INTERNAL_TOKEN="${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN:-$(rand 24)}"
fi
if [[ "${STORAGE}" == minio ]]; then
  export EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID="${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID:-$(rand 10)}"
  export EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY="${EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY:-$(rand 12)}"
fi

write_secrets() {
  umask 077
  {
    printf 'EDGE_PLATFORM_POSTGRES_PASSWORD=%s\n' "${EDGE_PLATFORM_POSTGRES_PASSWORD}"
    printf 'EDGE_PLATFORM_JWT_SECRET=%s\n' "${EDGE_PLATFORM_JWT_SECRET}"
    printf 'EDGE_PLATFORM_ADMIN_EMAIL=%s\n' "${EDGE_PLATFORM_ADMIN_EMAIL}"
    printf 'EDGE_PLATFORM_ADMIN_PASSWORD=%s\n' "${EDGE_PLATFORM_ADMIN_PASSWORD}"
    if [[ -n "${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-}" ]]; then
      printf 'EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN=%s\n' "${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}"
    fi
    if [[ -n "${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD:-}" ]]; then
      printf 'EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD=%s\n' "${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD}"
      printf 'EDGE_PLATFORM_MQTT_INTERNAL_TOKEN=%s\n' "${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN}"
    fi
    if [[ -n "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID:-}" ]]; then
      printf 'EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID=%s\n' "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID}"
      printf 'EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY=%s\n' "${EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY}"
    fi
  } > "$1"
  chmod 600 "$1"
}

write_secrets "${SECRETS}"
write_secrets "${HOME_SECRETS}"

{
  cat <<EOF
all:
  children:
    platform:
      hosts:
        meteorcloud-1:
          ansible_host: 127.0.0.1
          ansible_connection: local
          ansible_user: ${USER}
          ansible_python_interpreter: /usr/bin/python3
EOF
  if [[ -n "${PUBLIC_IP}" ]]; then
    printf '          public_ipv4: %s\n' "${PUBLIC_IP}"
  fi
  if [[ "${VPN}" -eq 1 ]]; then
    printf '          vpn_public_host: %s\n' "${VPN_HOST}"
  fi
} > "${INV}/hosts.yml"

STORAGE_PROVIDER=filesystem
MINIO_ENABLED=false
MQTT_ENABLED=false
MQTT_BROKER=bundled
MQTT_PUBLIC_HOST="${DOMAIN}"
case "${STORAGE}" in
  s3) STORAGE_PROVIDER=s3 ;;
  minio) STORAGE_PROVIDER=s3; MINIO_ENABLED=true; OBJECT_STORAGE_ENDPOINT_URL="http://minio:9000" ;;
esac
case "${MQTT}" in
  bundled)
    MQTT_ENABLED=true
    MQTT_BROKER=bundled
    MQTT_PUBLIC_HOST="${MQTT_HOST}"
    MQTT_BROKER_HOST=emqx
    ;;
  external)
    MQTT_ENABLED=true
    MQTT_BROKER=external
    MQTT_PUBLIC_HOST="${MQTT_BROKER_HOST}"
    ;;
esac

cat > "${INV}/group_vars/platform.yml" <<EOF
# Written by make up. Inventory is gitignored.
platform_public_url: ${PUBLIC_URL}
platform_domain: ${DOMAIN}
apps_base_domain: ${DOMAIN}
image_source: archive
platform_source_archive: ${ARCHIVE}
postgres_provider: bundled
cache_provider: memory
object_storage_provider: ${STORAGE_PROVIDER}
minio_enabled: ${MINIO_ENABLED}
object_storage_bucket: ${OBJECT_STORAGE_BUCKET}
object_storage_region: ${OBJECT_STORAGE_REGION}
object_storage_endpoint_url: ${OBJECT_STORAGE_ENDPOINT_URL}
mqtt_enabled: ${MQTT_ENABLED}
mqtt_broker: ${MQTT_BROKER}
mqtt_broker_host: ${MQTT_BROKER_HOST}
mqtt_broker_port: ${MQTT_BROKER_PORT}
mqtt_public_host: ${MQTT_PUBLIC_HOST}
firewall_enabled: false
EOF

echo "Packing this repository into ${ARCHIVE}"
if git -C "${REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "${REPO}" archive --format=tar.gz -o "${ARCHIVE}" HEAD
else
  tar -C "${REPO}" -czf "${ARCHIVE}" \
    --exclude=.git --exclude=.venv --exclude=node_modules --exclude=tests/e2e/.state .
fi

ANSIBLE_BIN="$(ansible_bin)"
echo "Using ${ANSIBLE_BIN}"
ensure_galaxy "${ANSIBLE_BIN}"

echo "Installing MeteorCloud on this VM (${PUBLIC_URL})"
cd "${HERE}"
PLAY_ARGS=(
  -e "${EXTRA_VARS}"
  -e "platform_source_archive=${ARCHIVE}"
  -e "platform_domain=${DOMAIN}"
  -e "platform_public_url=${PUBLIC_URL}"
  -e "postgres_password=${EDGE_PLATFORM_POSTGRES_PASSWORD}"
  -e "jwt_secret=${EDGE_PLATFORM_JWT_SECRET}"
  -e "admin_email=${EDGE_PLATFORM_ADMIN_EMAIL}"
  -e "admin_password=${EDGE_PLATFORM_ADMIN_PASSWORD}"
  -e "acme_email=${EDGE_PLATFORM_ACME_EMAIL}"
  -e "object_storage_provider=${STORAGE_PROVIDER}"
  -e "minio_enabled=${MINIO_ENABLED}"
  -e "mqtt_enabled=${MQTT_ENABLED}"
  -e "mqtt_broker=${MQTT_BROKER}"
)
if [[ "${VPN}" -eq 1 ]]; then
  PLAY_ARGS+=(-e "connectivity_internal_token=${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}")
fi
if [[ "${MQTT}" != off ]]; then
  PLAY_ARGS+=(
    -e "mqtt_platform_password=${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD}"
    -e "mqtt_internal_token=${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN}"
    -e "mqtt_broker_host=${MQTT_BROKER_HOST}"
    -e "mqtt_broker_port=${MQTT_BROKER_PORT}"
    -e "mqtt_public_host=${MQTT_PUBLIC_HOST}"
  )
fi
if [[ -n "${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID:-}" ]]; then
  PLAY_ARGS+=(
    -e "object_storage_access_key_id=${EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID}"
    -e "object_storage_secret_access_key=${EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY}"
  )
fi
"${ANSIBLE_BIN}" -i "${INV}/hosts.yml" playbooks/site.yml "${PLAY_ARGS[@]}"

echo
echo "MeteorCloud is running."
echo "  URL      ${PUBLIC_URL}"
echo "  health   ${PUBLIC_URL}/api/v1/health"
echo "Point DNS at this server, then open ${PUBLIC_URL} (not the raw IP)."
print_dns_guide "${DOMAIN}" "${PUBLIC_IP:-<this server public IP>}"
echo
echo "Login"
echo "  email     ${EDGE_PLATFORM_ADMIN_EMAIL}"
echo "  password  ${EDGE_PLATFORM_ADMIN_PASSWORD}"
echo
if [[ "${SECRETS_REUSED}" -eq 1 ]]; then
  echo "Secrets (reused; copy and keep):"
else
  echo "Secrets (generated for this install; copy and keep):"
fi
echo "  EDGE_PLATFORM_ADMIN_EMAIL=${EDGE_PLATFORM_ADMIN_EMAIL}"
echo "  EDGE_PLATFORM_ADMIN_PASSWORD=${EDGE_PLATFORM_ADMIN_PASSWORD}"
echo "  EDGE_PLATFORM_POSTGRES_PASSWORD=${EDGE_PLATFORM_POSTGRES_PASSWORD}"
echo "  EDGE_PLATFORM_JWT_SECRET=${EDGE_PLATFORM_JWT_SECRET}"
if [[ -n "${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-}" ]]; then
  echo "  EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN=${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}"
fi
echo
echo "Saved mode 0600 at:"
echo "  ${HOME_SECRETS}   (your copy, outside the repo)"
echo "  ${SECRETS}   (used by make up / make down)"
echo "Re-run make up to upgrade; the same secrets and setup are reused."

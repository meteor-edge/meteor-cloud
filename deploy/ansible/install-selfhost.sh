#!/usr/bin/env bash
# Install MeteorCloud on this VM / existing server.
# Host packages via Ansible (Ubuntu/Debian or RHEL/CentOS/Rocky/Alma 9+);
# the app itself runs in Docker.
# Clone the repo on the host, then from the repo root:
#
#   sudo apt-get install -y make    # Ubuntu/Debian, if make is missing
#   sudo dnf install -y make        # RHEL/CentOS/Rocky/Alma
#   make up
#   make up ARGS=--no-vpn
#   make down
#   make down ARGS=--destroy-data
#
# Or: ./deploy/ansible/install-selfhost.sh [--no-vpn|--down|--destroy-data]
#
# Creates inventory, secrets, and an Ansible venv if needed. Not the laptop
# development stack (`make dev`) and not creating a new EC2 (`make aws-up`).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
INV="${HERE}/inventory/selfhost"
SECRETS="${INV}/secrets.env"
HOME_DIR="${HOME}/.meteorcloud"
HOME_SECRETS="${HOME_DIR}/secrets.env"
ARCHIVE="${INV}/source.tar.gz"
VENV="${HOME}/.meteorcloud-ansible"
VPN=1
DOWN=0
DESTROY_DATA=0

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-vpn) VPN=0 ;;
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

detect_url() {
  if [[ -n "${PLATFORM_PUBLIC_URL:-}" ]]; then
    printf '%s\n' "${PLATFORM_PUBLIC_URL}"
    return
  fi
  local ip=""
  ip="$(curl -fsS --max-time 2 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  if [[ -z "${ip}" ]]; then
    ip="$(curl -fsS --max-time 2 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  if [[ -n "${ip}" ]]; then
    printf 'http://%s\n' "${ip}"
  else
    printf 'http://127.0.0.1\n'
  fi
}

PUBLIC_URL="$(detect_url)"
EXTRA_VARS='{"enabled_services":["cloud_app"]}'
if [[ "${VPN}" -eq 1 ]]; then
  EXTRA_VARS='{"enabled_services":["cloud_app","vpn"]}'
fi

mkdir -p "${INV}/group_vars" "${HOME_DIR}"
chmod 700 "${INV}" "${HOME_DIR}"

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

rand() { openssl rand -hex "$1"; }

export EDGE_PLATFORM_POSTGRES_PASSWORD="${EDGE_PLATFORM_POSTGRES_PASSWORD:-$(rand 16)}"
export EDGE_PLATFORM_JWT_SECRET="${EDGE_PLATFORM_JWT_SECRET:-$(rand 32)}"
export EDGE_PLATFORM_ADMIN_EMAIL="${EDGE_PLATFORM_ADMIN_EMAIL:-admin@localhost}"
export EDGE_PLATFORM_ADMIN_PASSWORD="${EDGE_PLATFORM_ADMIN_PASSWORD:-$(rand 12)}"
if [[ "${VPN}" -eq 1 ]]; then
  export EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN="${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-$(rand 24)}"
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
  } > "$1"
  chmod 600 "$1"
}

write_secrets "${SECRETS}"
write_secrets "${HOME_SECRETS}"

cat > "${INV}/hosts.yml" <<EOF
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

cat > "${INV}/group_vars/platform.yml" <<EOF
# Written by make up. Inventory is gitignored.
platform_public_url: ${PUBLIC_URL}
platform_domain: ""
image_source: archive
platform_source_archive: ${ARCHIVE}
postgres_provider: bundled
cache_provider: memory
object_storage_provider: filesystem
mqtt_enabled: false
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
  -e "postgres_password=${EDGE_PLATFORM_POSTGRES_PASSWORD}"
  -e "jwt_secret=${EDGE_PLATFORM_JWT_SECRET}"
  -e "admin_email=${EDGE_PLATFORM_ADMIN_EMAIL}"
  -e "admin_password=${EDGE_PLATFORM_ADMIN_PASSWORD}"
)
if [[ "${VPN}" -eq 1 ]]; then
  PLAY_ARGS+=(-e "connectivity_internal_token=${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}")
fi
"${ANSIBLE_BIN}" -i "${INV}/hosts.yml" playbooks/site.yml "${PLAY_ARGS[@]}"

echo
echo "MeteorCloud is running on this VM."
echo "  URL      ${PUBLIC_URL}"
echo "  health   ${PUBLIC_URL}/api/v1/health"
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
echo "Re-run make up to upgrade; the same secrets are reused."

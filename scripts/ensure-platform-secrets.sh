#!/usr/bin/env bash
# Fill missing EDGE_PLATFORM_* secrets (same idea as make up). Used by make aws-up.
#
#   ./scripts/ensure-platform-secrets.sh                 # generate / reuse, write file
#   ./scripts/ensure-platform-secrets.sh --report        # print saved values
#   ./scripts/ensure-platform-secrets.sh --run -- CMD    # ensure, export, run CMD
#   ./scripts/ensure-platform-secrets.sh --run --report -- CMD
set -euo pipefail

HOME_DIR="${METEORCLOUD_HOME:-${HOME}/.meteorcloud}"
SECRETS="${HOME_DIR}/secrets.env"
REPORT=0
RUN=0
CMD=()

usage() {
  cat <<EOF
Usage: $0 [--report] [--run -- command...]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report)
      REPORT=1
      shift
      ;;
    --run)
      RUN=1
      shift
      ;;
    --)
      shift
      CMD=("$@")
      break
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

rand() { openssl rand -hex "$1"; }

# Env already set by the caller wins over a saved file.
keep_pg="${EDGE_PLATFORM_POSTGRES_PASSWORD:-}"
keep_jwt="${EDGE_PLATFORM_JWT_SECRET:-}"
keep_conn="${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-}"
keep_mqtt_pw="${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD:-}"
keep_mqtt_token="${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN:-}"
keep_admin_email="${EDGE_PLATFORM_ADMIN_EMAIL:-}"
keep_admin_password="${EDGE_PLATFORM_ADMIN_PASSWORD:-}"

if [[ -f "${SECRETS}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${SECRETS}"
  set +a
fi

if [[ -n "${keep_pg}" ]]; then EDGE_PLATFORM_POSTGRES_PASSWORD="${keep_pg}"; fi
if [[ -n "${keep_jwt}" ]]; then EDGE_PLATFORM_JWT_SECRET="${keep_jwt}"; fi
if [[ -n "${keep_conn}" ]]; then EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN="${keep_conn}"; fi
if [[ -n "${keep_mqtt_pw}" ]]; then EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD="${keep_mqtt_pw}"; fi
if [[ -n "${keep_mqtt_token}" ]]; then EDGE_PLATFORM_MQTT_INTERNAL_TOKEN="${keep_mqtt_token}"; fi
if [[ -n "${keep_admin_email}" ]]; then EDGE_PLATFORM_ADMIN_EMAIL="${keep_admin_email}"; fi
if [[ -n "${keep_admin_password}" ]]; then EDGE_PLATFORM_ADMIN_PASSWORD="${keep_admin_password}"; fi

export EDGE_PLATFORM_POSTGRES_PASSWORD="${EDGE_PLATFORM_POSTGRES_PASSWORD:-$(rand 16)}"
export EDGE_PLATFORM_JWT_SECRET="${EDGE_PLATFORM_JWT_SECRET:-$(rand 32)}"
export EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN="${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN:-$(rand 24)}"
export EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD="${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD:-$(rand 16)}"
export EDGE_PLATFORM_MQTT_INTERNAL_TOKEN="${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN:-$(rand 24)}"
export EDGE_PLATFORM_ADMIN_EMAIL="${EDGE_PLATFORM_ADMIN_EMAIL:-admin@example.com}"
export EDGE_PLATFORM_ADMIN_PASSWORD="${EDGE_PLATFORM_ADMIN_PASSWORD:-$(rand 12)}"

mkdir -p "${HOME_DIR}"
chmod 700 "${HOME_DIR}"
umask 077
{
  printf 'EDGE_PLATFORM_POSTGRES_PASSWORD=%s\n' "${EDGE_PLATFORM_POSTGRES_PASSWORD}"
  printf 'EDGE_PLATFORM_JWT_SECRET=%s\n' "${EDGE_PLATFORM_JWT_SECRET}"
  printf 'EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN=%s\n' "${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}"
  printf 'EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD=%s\n' "${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD}"
  printf 'EDGE_PLATFORM_MQTT_INTERNAL_TOKEN=%s\n' "${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN}"
  printf 'EDGE_PLATFORM_ADMIN_EMAIL=%s\n' "${EDGE_PLATFORM_ADMIN_EMAIL}"
  printf 'EDGE_PLATFORM_ADMIN_PASSWORD=%s\n' "${EDGE_PLATFORM_ADMIN_PASSWORD}"
} > "${SECRETS}"
chmod 600 "${SECRETS}"

report() {
  echo
  echo "Secrets (copy and keep; saved mode 0600 at ${SECRETS}):"
  echo "  EDGE_PLATFORM_ADMIN_EMAIL=${EDGE_PLATFORM_ADMIN_EMAIL}"
  echo "  EDGE_PLATFORM_ADMIN_PASSWORD=${EDGE_PLATFORM_ADMIN_PASSWORD}"
  echo "  EDGE_PLATFORM_POSTGRES_PASSWORD=${EDGE_PLATFORM_POSTGRES_PASSWORD}"
  echo "  EDGE_PLATFORM_JWT_SECRET=${EDGE_PLATFORM_JWT_SECRET}"
  echo "  EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN=${EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN}"
  echo "  EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD=${EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD}"
  echo "  EDGE_PLATFORM_MQTT_INTERNAL_TOKEN=${EDGE_PLATFORM_MQTT_INTERNAL_TOKEN}"
  echo "Re-run make aws-up to reuse the same values."
}

if [[ "${RUN}" -eq 1 ]]; then
  if [[ ${#CMD[@]} -eq 0 ]]; then
    echo "error: --run requires a command after --" >&2
    exit 2
  fi
  set +e
  "${CMD[@]}"
  status=$?
  set -e
  if [[ "${REPORT}" -eq 1 ]]; then
    report
  fi
  exit "${status}"
fi

if [[ "${REPORT}" -eq 1 ]]; then
  report
fi

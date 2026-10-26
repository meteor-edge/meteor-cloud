#!/usr/bin/env bash
# Fill missing EDGE_PLATFORM_* secrets (same idea as make up). Used by make aws-up.
#
#   ./scripts/ensure-platform-secrets.sh                 # generate / reuse, write file
#   ./scripts/ensure-platform-secrets.sh --report        # print the login and where secrets are
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

# Variable (without EDGE_PLATFORM_) and how to make a missing value.
SPECS=(
  "POSTGRES_PASSWORD hex 16"
  "JWT_SECRET hex 32"
  "CONNECTIVITY_INTERNAL_TOKEN hex 24"
  "MQTT_PLATFORM_PASSWORD hex 16"
  "MQTT_INTERNAL_TOKEN hex 24"
  "ADMIN_EMAIL fixed admin@example.com"
  "ADMIN_PASSWORD hex 12"
)

# Parsed, not sourced: the file only holds KEY=value lines. Env set by the caller wins.
saved() {
  [[ -f "${SECRETS}" ]] || return 0
  sed -n "s/^$1=//p" "${SECRETS}" | tail -n 1
}

for spec in "${SPECS[@]}"; do
  read -r name kind arg <<<"${spec}"
  var="EDGE_PLATFORM_${name}"
  value="${!var:-$(saved "${var}")}"
  if [[ -z "${value}" ]]; then
    if [[ "${kind}" == hex ]]; then value="$(openssl rand -hex "${arg}")"; else value="${arg}"; fi
  fi
  export "${var}=${value}"
done

mkdir -p "${HOME_DIR}"
chmod 700 "${HOME_DIR}"
umask 077
for spec in "${SPECS[@]}"; do
  var="EDGE_PLATFORM_${spec%% *}"
  printf '%s=%s\n' "${var}" "${!var}"
done > "${SECRETS}"
chmod 600 "${SECRETS}"

# Only the login: the other secrets stay in the 0600 file, out of terminals and CI logs.
report() {
  echo
  echo "Login:   ${EDGE_PLATFORM_ADMIN_EMAIL} / ${EDGE_PLATFORM_ADMIN_PASSWORD}"
  echo "Secrets: ${SECRETS} (mode 0600; re-run make aws-up to reuse them)"
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

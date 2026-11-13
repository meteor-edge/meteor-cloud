#!/usr/bin/env bash
# Local MQTT integration, security, and ping/pong tests against the bundled broker in
# Docker Compose. MQTT_PROVIDER picks it: mosquitto (default), emqx, or vernemq.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/tests${PYTHONPATH:+:$PYTHONPATH}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
  export PATH="$ROOT/.venv/bin:$PATH"
else
  PYTHON="${PYTHON:-python3}"
fi

ENV_FILE="$ROOT/deploy/compose/.env"
COMPOSE="${MQTT_COMPOSE:-docker compose -f $ROOT/deploy/compose/docker-compose.yml}"
export MQTT_COMPOSE="$COMPOSE"
export PLATFORM_URL="${PLATFORM_URL:-http://127.0.0.1:8000}"
export MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
export MQTT_PORT="${MQTT_PORT:-8883}"
export MQTT_CA_FILE="${MQTT_CA_FILE:-$ROOT/certs/ca.crt}"
export MQTT_PROVIDER="${MQTT_PROVIDER:-mosquitto}"
case "$MQTT_PROVIDER" in
  mosquitto|emqx|vernemq) ;;
  *) echo "error: MQTT_PROVIDER must be mosquitto, emqx, or vernemq" >&2; exit 1 ;;
esac
BROKER="$MQTT_PROVIDER"
export MQTT_BROKER_SERVICE="$BROKER"
if [[ "$BROKER" == "vernemq" && "${VERNEMQ_ACCEPT_EULA:-}" != "yes" ]]; then
  echo "error: the official VerneMQ image needs VERNEMQ_ACCEPT_EULA=yes (see docs/licensing.md)" >&2
  exit 1
fi
export MQTT_ALLOW_BROKER_RESTART="${MQTT_ALLOW_BROKER_RESTART:-1}"
# The suite registers many devices from one address within a minute.
export REGISTRATION_RATE_LIMIT_REQUESTS="${REGISTRATION_RATE_LIMIT_REQUESTS:-1000}"
export ENROLLMENT_REQUEST_RATE_LIMIT_REQUESTS="${ENROLLMENT_REQUEST_RATE_LIMIT_REQUESTS:-1000}"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/deploy/compose/.env.example" "$ENV_FILE"
fi
if [[ -z "${MQTT_INTERNAL_TOKEN:-}" ]]; then
  MQTT_INTERNAL_TOKEN="$(awk -F= '/^MQTT_INTERNAL_TOKEN=/{print substr($0, index($0,"=")+1); exit}' "$ENV_FILE")"
  export MQTT_INTERNAL_TOKEN
fi
if [[ -z "${MQTT_INTERNAL_TOKEN:-}" ]]; then
  echo "error: MQTT_INTERNAL_TOKEN must be set (environment or deploy/compose/.env)" >&2
  exit 1
fi
./scripts/generate-local-mqtt-certs.sh

dump_broker() {
  echo "==> Broker diagnostics ($BROKER)"
  # shellcheck disable=SC2086
  $COMPOSE ps || true
  # shellcheck disable=SC2086
  $COMPOSE logs "$BROKER" --tail 120 || true
  # shellcheck disable=SC2086
  $COMPOSE logs backend --tail 40 || true
}

echo "==> Starting postgres, redis, backend, $BROKER, data-plane"
BUILD_ARGS=()
if [[ "${MQTT_COMPOSE_BUILD:-}" == "1" ]]; then
  BUILD_ARGS+=(--build)
fi
# shellcheck disable=SC2086
if $COMPOSE up --help 2>&1 | grep -q -- '--wait'; then
  # shellcheck disable=SC2086
  if ! $COMPOSE up -d "${BUILD_ARGS[@]}" --wait --wait-timeout 180 postgres redis backend "$BROKER" data-plane; then
    dump_broker
    exit 1
  fi
else
  echo "==> Compose --wait unavailable; starting without --wait"
  # shellcheck disable=SC2086
  $COMPOSE up -d "${BUILD_ARGS[@]}" postgres redis backend "$BROKER" data-plane
fi

cleanup() {
  if [[ "${MQTT_COMPOSE_DOWN:-}" == "1" ]]; then
    echo "==> Stopping Compose services"
    # shellcheck disable=SC2086
    $COMPOSE down
  fi
}
trap cleanup EXIT

echo "==> Waiting for backend, data-plane, and MQTT TLS"
if ! "$PYTHON" - <<'PY'
from mqtt_live.config import load_config
from mqtt_live.http import wait_http, wait_mqtt_tls

cfg = load_config()
wait_http(f"{cfg.platform_url}/health", timeout_seconds=180)
wait_http("http://127.0.0.1:8081/health", timeout_seconds=180)
wait_mqtt_tls(cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_ca_file, timeout_seconds=180)
print("backend, data-plane, and MQTT TLS are ready")
PY
then
  echo "==> $BROKER did not become reachable on MQTT TLS"
  dump_broker
  exit 1
fi

echo "==> MQTT integration + ping/pong"
"$PYTHON" -m pytest -q tests/mqtt_live
echo "==> MQTT tests passed"

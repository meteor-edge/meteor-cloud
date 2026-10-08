#!/usr/bin/env bash
# Starts the minimal production Compose stack (PostgreSQL, API, console,
# Traefik; filesystem storage, no Redis/MinIO/MQTT) under its own project
# name and ports, runs scripts/smoke_test.py, and removes everything again.
# Uses existing images meteorcloud/{backend,console}:$IMAGE_TAG (make images).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/deploy/compose"

PROJECT="${SMOKE_PROJECT:-meteorcloud-smoke}"
HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
ENV_FILE=".env.smoke"

secret() { python3 -c "import secrets; print(secrets.token_hex($1))"; }
JWT_SECRET="$(secret 32)"

cat > "$ENV_FILE" <<ENV
COMPOSE_PROJECT_NAME=$PROJECT
COMPOSE_NETWORK_NAME=$PROJECT
COMPOSE_PROFILES=postgres
BACKEND_IMAGE=meteorcloud/backend:$IMAGE_TAG
CONSOLE_IMAGE=meteorcloud/console:$IMAGE_TAG
DATA_PLANE_IMAGE=meteorcloud/data-plane:$IMAGE_TAG
IMAGE_PULL_POLICY=never
HTTP_PORT=$HTTP_PORT
HTTPS_PORT=${SMOKE_HTTPS_PORT:-18443}
POSTGRES_PASSWORD=$(secret 16)
APP_SECRET_KEY=$JWT_SECRET
JWT_SECRET_KEY=$JWT_SECRET
APP_ENV=ci
LOG_FORMAT=json
REGISTRATION_REQUIRE_HTTPS=false
CACHE_PROVIDER=memory
OBJECT_STORAGE_PROVIDER=filesystem
MQTT_ENABLED=false
ENV
chmod 600 "$ENV_FILE"
export METEORCLOUD_ENV_FILE="$ENV_FILE"

compose() {
  docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml "$@"
}

cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

echo "==> Starting minimal production stack ($PROJECT, http://127.0.0.1:$HTTP_PORT)"
if ! compose up -d --wait --wait-timeout 300; then
  compose ps
  compose logs --tail 200
  exit 1
fi
compose ps

echo "==> Smoke test"
python3 "$ROOT/scripts/smoke_test.py" "http://127.0.0.1:$HTTP_PORT"

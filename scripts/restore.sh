#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh into a running Compose deployment.
# This REPLACES the current PostgreSQL database, ClickHouse tables and artifacts.
#
#   scripts/restore.sh --yes backups/20261116T030000Z
#
# Run it from deploy/compose (or set COMPOSE_DIR) with the stack up. Application
# services (API, data plane, workers) are stopped while data is replaced and started
# again afterwards. Same environment variables as scripts/backup.sh (COMPOSE_DIR, COMPOSE).
# See docs/backups.md.
set -euo pipefail

usage() { echo "usage: $0 --yes <backup directory>" >&2; exit 2; }
[ "${1:-}" = "--yes" ] || usage
SOURCE="${2:-}"
[ -n "$SOURCE" ] || usage
SOURCE="$(cd "$SOURCE" && pwd)"
[ -f "$SOURCE/manifest.json" ] || { echo "error: $SOURCE has no manifest.json; not a backup directory" >&2; exit 1; }

cd "${COMPOSE_DIR:-.}"
read -r -a COMPOSE_CMD <<< "${COMPOSE:-docker compose -f docker-compose.yml -f docker-compose.prod.yml}"
compose() { "${COMPOSE_CMD[@]}" "$@"; }

LOCK_FILE="${BACKUP_LOCK_FILE:-${BACKUP_DIR:-./backups}/.lock}"
mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "error: another backup or restore holds $LOCK_FILE" >&2
  exit 1
fi

running="$(compose ps --services --status running)"
has() { grep -qx "$1" <<< "$running"; }
need() { has "$1" || { echo "error: the backup has $2 but the $1 service is not running" >&2; exit 1; }; }

[ -f "$SOURCE/postgres.dump" ] && need postgres "a PostgreSQL dump"
[ -d "$SOURCE/clickhouse" ] && need clickhouse "ClickHouse tables"
[ -f "$SOURCE/minio.tar.gz" ] && need minio "MinIO data"
[ -f "$SOURCE/artifacts.tar.gz" ] && need backend "filesystem artifacts"

# Stop everything that writes to the databases or storage before replacing any of it.
stateful="postgres redis clickhouse minio nats mosquitto emqx vernemq traefik"
writers=()
for service in $running; do
  case " $stateful " in *" $service "*) ;; *) writers+=("$service") ;; esac
done
if [ "${#writers[@]}" -gt 0 ]; then
  echo "==> Stopping ${writers[*]}"
  compose stop "${writers[@]}"
fi

# Filesystem artifacts: backend is stopped; use a one-shot container with the same volume.
if [ -f "$SOURCE/artifacts.tar.gz" ]; then
  echo "==> Artifacts (filesystem storage)"
  compose run --rm --no-deps -T --entrypoint sh backend -c \
    'find /data/artifacts -mindepth 1 -delete && tar -C /data/artifacts --no-same-owner -xzf -' \
    < "$SOURCE/artifacts.tar.gz"
fi

if [ -f "$SOURCE/minio.tar.gz" ]; then
  echo "==> Artifacts (bundled MinIO)"
  compose exec -T minio sh -c 'find /bitnami/minio/data -mindepth 1 -delete && tar -C /bitnami/minio/data --no-same-owner -xzf -' \
    < "$SOURCE/minio.tar.gz"
  compose restart minio
fi

if [ -f "$SOURCE/postgres.dump" ]; then
  echo "==> PostgreSQL"
  compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" \
    && createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'
  compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' \
    < "$SOURCE/postgres.dump"
fi

if [ -d "$SOURCE/clickhouse" ]; then
  echo "==> ClickHouse"
  ch() { compose exec -T clickhouse sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database "$CLICKHOUSE_DB" "$@"' ch "$@"; }
  # Drop views first so inserting into their source tables does not feed them twice.
  for view in $(ch --query "SELECT name FROM system.tables WHERE database = currentDatabase() AND engine LIKE '%View'"); do
    ch --query "DROP VIEW IF EXISTS \`$view\` SYNC"
  done
  shopt -s nullglob
  table_schemas=("$SOURCE"/clickhouse/*.sql)
  view_schemas=("$SOURCE"/clickhouse/*.view.sql)
  shopt -u nullglob
  for schema in "${table_schemas[@]}"; do
    case "$schema" in *.view.sql) continue ;; esac
    table="$(basename "$schema" .sql)"
    ch --query "DROP TABLE IF EXISTS \`$table\` SYNC"
    ch --multiquery < "$schema"
    gzip -dc "$SOURCE/clickhouse/$table.native.gz" | ch --query "INSERT INTO \`$table\` FORMAT Native"
  done
  for schema in "${view_schemas[@]}"; do
    ch --multiquery < "$schema"
  done
fi

if [ "${#writers[@]}" -gt 0 ]; then
  echo "==> Starting ${writers[*]}"
  compose up -d --wait --wait-timeout 300 "${writers[@]}"
fi
echo "==> Restored $SOURCE"

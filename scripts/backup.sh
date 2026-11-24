#!/usr/bin/env bash
# Back up a Compose deployment of MeteorCloud: PostgreSQL, ClickHouse and artifact
# storage (filesystem volume or bundled MinIO). Run it from deploy/compose (or set
# COMPOSE_DIR); only services that are running are backed up.
#
#   scripts/backup.sh                       # writes ./backups/<UTC timestamp>/
#   BACKUP_DIR=/opt/edge-platform/backups scripts/backup.sh
#
# Environment:
#   COMPOSE_DIR              directory with docker-compose.yml (default: current directory)
#   COMPOSE                  Compose command (default: docker compose -f docker-compose.yml -f docker-compose.prod.yml)
#   BACKUP_DIR               where backups go (default: ./backups)
#   BACKUP_LOCK_FILE         exclusive lock shared with restore (default: $BACKUP_DIR/.lock)
#   BACKUP_RETENTION_DAYS    delete local backups older than this (default: 7; 0 keeps all)
#   BACKUP_S3_URI            optional s3://bucket/prefix to copy each backup to (uses the aws-cli image)
#   BACKUP_S3_ENDPOINT_URL   optional endpoint for S3-compatible storage (MinIO, R2, Ceph)
#   BACKUP_AWS_CLI_IMAGE     image for the upload (default: amazon/aws-cli:2.22.0)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN / AWS_REGION are passed to it.
#
# Restore with scripts/restore.sh <backup directory>. See docs/backups.md.
set -euo pipefail
umask 077

cd "${COMPOSE_DIR:-.}"
read -r -a COMPOSE_CMD <<< "${COMPOSE:-docker compose -f docker-compose.yml -f docker-compose.prod.yml}"
compose() { "${COMPOSE_CMD[@]}" "$@"; }

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION="${BACKUP_RETENTION_DAYS:-7}"
LOCK_FILE="${BACKUP_LOCK_FILE:-$BACKUP_DIR/.lock}"
# PID makes concurrent runs within the same second use distinct destinations.
NAME="$(date -u +%Y%m%dT%H%M%SZ).$$"
TARGET="$BACKUP_DIR/$NAME"
PARTIAL="$BACKUP_DIR/.$NAME.partial"

mkdir -p "$BACKUP_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "error: another backup or restore holds $LOCK_FILE" >&2
  exit 1
fi

rm -rf "$PARTIAL"
mkdir -p "$PARTIAL"
writers=()
writers_stopped=0
cleanup() {
  rm -rf "$PARTIAL"
  if [ "$writers_stopped" -eq 1 ] && [ "${#writers[@]}" -gt 0 ]; then
    echo "==> Starting ${writers[*]} after backup failure" >&2
    compose up -d --wait --wait-timeout 300 "${writers[@]}" || true
  fi
}
trap cleanup EXIT

running="$(compose ps --services --status running)"
has() { grep -qx "$1" <<< "$running"; }
components=()

# Application services that write to databases or artifact storage (same set as restore.sh).
stateful="postgres redis clickhouse minio nats mosquitto emqx vernemq traefik"
for service in $running; do
  case " $stateful " in *" $service "*) ;; *) writers+=("$service") ;; esac
done
stop_writers() {
  if [ "${#writers[@]}" -gt 0 ]; then
    echo "==> Stopping ${writers[*]}"
    compose stop "${writers[@]}"
    writers_stopped=1
  fi
}
start_writers() {
  if [ "${#writers[@]}" -gt 0 ]; then
    echo "==> Starting ${writers[*]}"
    compose up -d --wait --wait-timeout 300 "${writers[@]}"
    writers_stopped=0
    running="$(compose ps --services --status running)"
  fi
}

if has postgres; then
  echo "==> PostgreSQL"
  compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$PARTIAL/postgres.dump"
  components+=(postgres)
else
  echo "==> PostgreSQL: bundled service not running (external database: use your provider's backups)"
fi

if has clickhouse; then
  echo "==> ClickHouse"
  mkdir -p "$PARTIAL/clickhouse"
  # Pause writers so table dumps share one state (materialized views do not race inserts).
  stop_writers
  # One schema file and one Native dump per table; views are recreated from the schema.
  ch() { compose exec -T clickhouse sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database "$CLICKHOUSE_DB" "$@"' ch "$@"; }
  tables="$(ch --query "SELECT name FROM system.tables WHERE database = currentDatabase() AND engine NOT LIKE '%View' AND name NOT LIKE '.inner%' AND NOT is_temporary ORDER BY name")"
  views="$(ch --query "SELECT name FROM system.tables WHERE database = currentDatabase() AND engine LIKE '%View' ORDER BY name")"
  for table in $tables; do
    ch --query "SHOW CREATE TABLE \`$table\`" --format TSVRaw > "$PARTIAL/clickhouse/$table.sql"
    ch --query "SELECT * FROM \`$table\` FORMAT Native" | gzip -1 > "$PARTIAL/clickhouse/$table.native.gz"
  done
  for view in $views; do
    ch --query "SHOW CREATE TABLE \`$view\`" --format TSVRaw > "$PARTIAL/clickhouse/$view.view.sql"
  done
  start_writers
  components+=(clickhouse)
fi

if has minio; then
  echo "==> Artifacts (bundled MinIO)"
  compose exec -T minio tar -C /bitnami/minio/data -czf - . > "$PARTIAL/minio.tar.gz"
  components+=(minio)
elif has backend && compose exec -T backend test -d /data/artifacts; then
  echo "==> Artifacts (filesystem storage)"
  compose exec -T backend tar -C /data/artifacts -czf - . > "$PARTIAL/artifacts.tar.gz"
  components+=(artifacts)
fi

if [ "${#components[@]}" -eq 0 ]; then
  echo "error: nothing to back up (no postgres, clickhouse, minio or backend service running)" >&2
  exit 1
fi

if [ -e "$TARGET" ]; then
  echo "error: backup destination already exists: $TARGET" >&2
  exit 1
fi

printf '{"created_at": "%s", "components": [%s]}\n' "$NAME" \
  "$(printf '"%s",' "${components[@]}" | sed 's/,$//')" > "$PARTIAL/manifest.json"
mv "$PARTIAL" "$TARGET"
trap - EXIT
writers_stopped=0
echo "==> Backup written to $TARGET ($(du -sh "$TARGET" | cut -f1))"

if [ -n "${BACKUP_S3_URI:-}" ]; then
  echo "==> Copying to ${BACKUP_S3_URI%/}/$NAME/"
  endpoint=()
  [ -n "${BACKUP_S3_ENDPOINT_URL:-}" ] && endpoint=(--endpoint-url "$BACKUP_S3_ENDPOINT_URL")
  docker run --rm -v "$(cd "$TARGET" && pwd):/backup:ro" \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION -e AWS_DEFAULT_REGION \
    "${BACKUP_AWS_CLI_IMAGE:-amazon/aws-cli:2.22.0}" "${endpoint[@]}" \
    s3 cp --recursive --only-show-errors /backup "${BACKUP_S3_URI%/}/$NAME/"
fi

if [ "$RETENTION" -gt 0 ]; then
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*Z*' -mtime +"$RETENTION" -print -exec rm -rf {} +
fi

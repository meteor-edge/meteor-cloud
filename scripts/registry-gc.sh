#!/usr/bin/env bash
# Free the storage of deleted images (docs/registry.md). The registry is stopped meanwhile:
# garbage collection during a push can delete the layers being pushed.
#
#   scripts/registry-gc.sh              # run it from deploy/compose, like scripts/backup.sh
#   scripts/registry-gc.sh --dry-run    # only show what would be deleted
#
# Environment: COMPOSE_DIR, COMPOSE (as for scripts/backup.sh)
set -euo pipefail

cd "${COMPOSE_DIR:-.}"
read -r -a COMPOSE_CMD <<< "${COMPOSE:-docker compose -f docker-compose.yml -f docker-compose.prod.yml}"
compose() { "${COMPOSE_CMD[@]}" "$@"; }

args=(--delete-untagged --quiet)
case "${1:-}" in
"") ;;
--dry-run) args=(--delete-untagged --dry-run) ;;
*) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

# Images that releases use get their hidden tags, deleted organizations lose theirs. When this
# fails nothing is collected: an image a device needs for a rollback could be lost.
compose exec -T backend python -m app.cli registry-reconcile

trap 'compose start registry' EXIT
compose stop registry
compose run --rm --no-deps -T registry garbage-collect "${args[@]}" /etc/distribution/config.yml

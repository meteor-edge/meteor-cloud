#!/bin/sh
# Run inside the bundled postgres container (docker compose exec -T postgres sh -s -- check|align).
#
# The official image applies POSTGRES_PASSWORD only on first init. A later
# secret change leaves the volume with the old role password while Compose
# already injects the new one. Local/unix-socket connections stay "trust",
# so we can reset the role without knowing the previous secret.
set -eu

sql_literal() {
  printf '%s' "$1" | sed "s/'/''/g"
}

sql_ident() {
  printf '%s' "$1" | sed 's/"/""/g'
}

cmd="${1:-align}"
user="${POSTGRES_USER:?}"
db="${POSTGRES_DB:?}"
password="${POSTGRES_PASSWORD:?}"

case "$cmd" in
  check)
    # Service hostname resolves to the container's network address, which uses
    # scram-sha-256 (127.0.0.1 and the unix socket are trust in this image).
    PGPASSWORD="$password" psql -h postgres -U "$user" -d "$db" -c 'SELECT 1' >/dev/null
    ;;
  align)
    ident=$(sql_ident "$user")
    lit=$(sql_literal "$password")
    psql -U "$user" -d "$db" -v ON_ERROR_STOP=1 \
      -c "ALTER ROLE \"${ident}\" PASSWORD '${lit}'"
    ;;
  *)
    echo "usage: $0 check|align" >&2
    exit 2
    ;;
esac

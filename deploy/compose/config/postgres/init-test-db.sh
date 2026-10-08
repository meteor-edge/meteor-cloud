#!/bin/sh
# Create a sibling *_test database so pytest never truncates application data.
# The official Postgres image only runs this on first volume init; pytest also
# creates the database if it is missing.
set -e

if [ -z "${POSTGRES_DB}" ] || [ -z "${POSTGRES_USER}" ]; then
  exit 0
fi

case "${POSTGRES_DB}" in
  *_test)
    exit 0
    ;;
esac

TEST_DB="${POSTGRES_DB}_test"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<EOSQL
SELECT 'CREATE DATABASE "${TEST_DB}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${TEST_DB}')\gexec
EOSQL

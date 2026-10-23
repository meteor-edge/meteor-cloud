#!/usr/bin/env bash
# A MeteorCloud demo environment with seed data, in one command. Settings: tests/e2e/.env.
#
#   tests/e2e/demo.sh up       servers + DNS (Terraform), install (Ansible), demo data (seed)
#   tests/e2e/demo.sh status   URLs, DNS records, logins, SSH commands
#   tests/e2e/demo.sh down     destroy every server and DNS record of this environment
set -euo pipefail

TESTENV="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/meteorcloud-testenv"

case "${1:-}" in
  up) "${TESTENV}" up --no-tests && "${TESTENV}" seed && "${TESTENV}" status ;;
  status) "${TESTENV}" status ;;
  down) "${TESTENV}" down ;;
  *)
    sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac

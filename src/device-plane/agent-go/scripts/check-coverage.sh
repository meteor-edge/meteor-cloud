#!/usr/bin/env bash
# Fail when a listed package has less statement coverage than the minimum.
# Packages that do not exist yet are skipped, so the list can name packages
# that later pull requests add (identity, deploy, update, helper).
#
#   scripts/check-coverage.sh <go test -cover output> <min percent> <package>...
set -euo pipefail

report="${1:?usage: check-coverage.sh <report> <min> <package>...}"
min="${2:?}"
shift 2

status=0
for pkg in "$@"; do
  line=$(grep -E "/internal/$pkg[[:space:]].*coverage:" "$report" || true)
  if [ -z "$line" ]; then
    echo "skip  $pkg (no such package yet)"
    continue
  fi
  pct=$(sed -E 's/.*coverage: ([0-9.]+)%.*/\1/' <<<"$line")
  if awk -v p="$pct" -v m="$min" 'BEGIN { exit !(p < m) }'; then
    echo "FAIL  $pkg ${pct}% < ${min}%"
    status=1
  else
    echo "ok    $pkg ${pct}%"
  fi
done
exit "$status"

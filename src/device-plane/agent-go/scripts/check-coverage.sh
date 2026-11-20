#!/usr/bin/env bash
# Fail when a listed package has less statement coverage than the minimum.
# A package whose directory does not exist yet is skipped, so the list can name
# packages that later pull requests add (identity, deploy, update, helper). An
# existing package without a coverage line, or a missing report, fails.
#
#   scripts/check-coverage.sh <go test -cover output> <min percent> <package>...
set -euo pipefail

report="${1:?usage: check-coverage.sh <report> <min> <package>...}"
min="${2:?}"
shift 2

if [ ! -s "$report" ] || [ ! -r "$report" ]; then
  echo "FAIL  coverage report $report is missing, empty or unreadable" >&2
  exit 1
fi

cd "$(dirname "$0")/.."
status=0
for pkg in "$@"; do
  if [ ! -d "internal/$pkg" ]; then
    echo "skip  $pkg (no such package yet)"
    continue
  fi
  line=$(grep -E "/internal/$pkg[[:space:]].*coverage: [0-9.]+%" "$report" | head -n 1) || line=""
  if [ -z "$line" ]; then
    echo "FAIL  $pkg has no coverage result in $report"
    status=1
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

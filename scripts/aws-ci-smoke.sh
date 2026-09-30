#!/usr/bin/env bash
# Smoke-check a deployed Edge Platform instance.
set -euo pipefail

URL="${1:?usage: aws-ci-smoke.sh <platform-url>}"
BASE="${URL%/}"

check() {
  local path="$1"
  local label="$2"
  local code
  code=$(curl -fsS -o /tmp/aws-ci-smoke.body -w "%{http_code}" --max-time 20 "${BASE}${path}" || true)
  if [[ "${code}" != 2* ]]; then
    echo "FAIL ${label}: ${BASE}${path} -> HTTP ${code:-000}"
    if [[ -s /tmp/aws-ci-smoke.body ]]; then
      head -c 400 /tmp/aws-ci-smoke.body
      echo
    fi
    return 1
  fi
  echo "OK   ${label}: HTTP ${code}"
}

echo "Smoke checks against ${BASE}"
check "/health" "backend health"
check "/api/v1/health" "API health" || check "/health" "API health fallback"
check "/" "console"
echo "All smoke checks passed."

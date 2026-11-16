#!/bin/sh
# Simulated device: enroll once (registration token or enrollment API key), then run the
# real agent. Hardware facts come from METEORCLI_DEVICE_PROFILE (docs/fleet/simulator.md).
set -eu

: "${METEORCLI_SERVER:?Set METEORCLI_SERVER to the control-plane URL, e.g. http://localhost:8000}"
dir="${METEORCLI_CONFIG_DIR:-/var/lib/meteorcli}"
mkdir -p "$dir"

if [ ! -s "$dir/device-token" ] && [ ! -s "$dir/claim-secret" ]; then
  if [ -n "${METEORCLI_TOKEN:-}" ]; then
    if [ -n "${METEORCLI_DEVICE_NAME:-}" ]; then
      meteorcli register --server "$METEORCLI_SERVER" --name "$METEORCLI_DEVICE_NAME"
    else
      meteorcli register --server "$METEORCLI_SERVER"
    fi
  elif [ -n "${METEORCLI_API_KEY:-}" ]; then
    # Submit a token request; `meteorcli run` waits for an administrator to approve it.
    meteorcli request-token --server "$METEORCLI_SERVER" --wait 0
  else
    echo "Set METEORCLI_TOKEN (registration token) or METEORCLI_API_KEY (enrollment API key)." >&2
    exit 2
  fi
fi

exec meteorcli run

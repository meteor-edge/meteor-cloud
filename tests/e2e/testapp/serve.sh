#!/bin/sh
set -eu
mkdir -p /tmp/www
printf '{"app":"meteorcloud-e2e","run_id":"%s","device_id":"%s","version":"%s"}\n' \
  "${RUN_ID:-}" "${DEVICE_ID:-}" "${VERSION:-}" > /tmp/www/index.html
exec httpd -f -p 8080 -h /tmp/www

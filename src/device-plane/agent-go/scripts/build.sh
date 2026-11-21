#!/usr/bin/env bash
# Build static meteorcli binaries for the boards we support and fail when one
# grows past the size budget (15 MB).
#
#   scripts/build.sh <out-dir> [version]
#
# $METEORCLI_RELEASE_KEYS replaces the release public key(s) compiled into the
# updater (test builds signed with a test key; see tools/release).
set -euo pipefail

out="${1:?usage: build.sh <out-dir> [version]}"
version="${2:-0.0.0-dev}"
max_bytes=$((15 * 1024 * 1024))
ldflags="-s -w -X main.version=$version"
if [ -n "${METEORCLI_RELEASE_KEYS:-}" ]; then
  ldflags="$ldflags -X meteorcloud/meteorcli/internal/update.releaseKeys=$METEORCLI_RELEASE_KEYS"
  echo "note: test build; it trusts release key(s) $METEORCLI_RELEASE_KEYS"
fi

cd "$(dirname "$0")/.."
mkdir -p "$out"

# GOARCH:GOARM — arm v6 covers the Raspberry Pi Zero and 1.
for target in arm64: arm:6 amd64:; do
  arch="${target%%:*}"
  arm="${target#*:}"
  bin="$out/meteorcli-linux-$arch"
  [ -n "$arm" ] && bin="${bin}v$arm"
  CGO_ENABLED=0 GOOS=linux GOARCH="$arch" GOARM="$arm" \
    go build -trimpath -ldflags="$ldflags" -o "$bin" ./cmd/meteorcli
  size=$(wc -c <"$bin")
  echo "$bin: $size bytes"
  if [ "$size" -gt "$max_bytes" ]; then
    echo "error: $bin is larger than 15 MB" >&2
    exit 1
  fi
done

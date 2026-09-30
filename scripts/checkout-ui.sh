#!/usr/bin/env bash
# Copy console/ and website/ from the private meteor-ui repo into this tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_REMOTE="${METEOR_UI_REMOTE:-git@github.com:meteor-edge/meteor-ui.git}"
UI_REF="${METEOR_UI_REF:-main}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Cloning ${UI_REMOTE} (${UI_REF})..."
git clone --depth 1 --branch "$UI_REF" "$UI_REMOTE" "$TMP"

mkdir -p "$ROOT/console" "$ROOT/website"
rsync -a --delete --exclude README.md "$TMP/console/" "$ROOT/console/"
rsync -a --delete --exclude README.md "$TMP/website/" "$ROOT/website/"

echo "Console and website source are in place (gitignored in this repo)."
echo "  make install-console install-website"
echo "  make dev-console     # http://localhost:5173"
echo "  make dev-website     # http://localhost:3000"

#!/usr/bin/env bash
# Release files for the Go meteorcli: binaries, the packaging files, one
# installer per board, and SHA256SUMS. CI publishes them as the
# meteorcli-go-edge prerelease; you can also serve the directory yourself and
# point an installer at it (--base-url http://laptop:8000 or file:///path).
#
#   scripts/dist.sh <out-dir> <version> [base-url]
#
# base-url defaults to the meteorcli-go-edge release of meteor-edge/meteor-cloud.
set -euo pipefail

out="${1:?usage: dist.sh <out-dir> <version> [base-url]}"
version="${2:?usage: dist.sh <out-dir> <version> [base-url]}"
base="${3:-https://github.com/meteor-edge/meteor-cloud/releases/download/meteorcli-go-edge}"

cd "$(dirname "$0")/.."
mkdir -p "$out"
out="$(cd "$out" && pwd)"

./scripts/build.sh "$out" "$version"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/packaging"
cp -r packaging/meteorcli.service packaging/meteorcli-helper.service packaging/meteorcli-helper.socket \
	packaging/sysusers.d packaging/tmpfiles.d "$stage/packaging/"
tar -C "$stage" --owner=0 --group=0 --numeric-owner -czf "$out/meteorcli-packaging.tar.gz" packaging

# Boards with an installer: <script suffix>:<board name as platform.Detect calls it>.
for entry in rpi:raspberry-pi; do
	suffix="${entry%%:*}"
	board="${entry#*:}"
	sed -e "s|@BOARD@|$board|" -e "s|@BASE_URL@|$base|" packaging/install/install.sh >"$out/install-$suffix.sh"
	chmod 0755 "$out/install-$suffix.sh"
done

(cd "$out" && sha256sum meteorcli-linux-* meteorcli-packaging.tar.gz install-*.sh >SHA256SUMS)
echo "release files in $out:"
ls -l "$out"

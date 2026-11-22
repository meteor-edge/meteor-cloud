#!/usr/bin/env bash
# Release files for the Go meteorcli: binaries, the signed release manifest
# (meteorcli-release.json + .sig, read by `meteorcli updater`), the packaging
# files, one installer per board, and SHA256SUMS. CI publishes them as the
# meteorcli-go-edge prerelease; you can also serve the directory yourself and
# point an installer at it (--base-url http://laptop:8000 or file:///path).
#
#   scripts/dist.sh <out-dir> <version> [base-url]
#
# base-url defaults to the meteorcli-go-edge release of meteor-edge/meteor-cloud.
# The manifest is signed with $METEORCLI_RELEASE_SIGNING_KEY (go run ./tools/release
# genkey); without it the release is unsigned and devices refuse to update to it.
set -euo pipefail

out="${1:?usage: dist.sh <out-dir> <version> [base-url]}"
version="${2:?usage: dist.sh <out-dir> <version> [base-url]}"
base="${3:-https://github.com/meteor-edge/meteor-cloud/releases/download/meteorcli-go-edge}"

cd "$(dirname "$0")/.."
mkdir -p "$out"
out="$(cd "$out" && pwd)"

./scripts/build.sh "$out" "$version"
rm -f "$out/meteorcli-release.json" "$out/meteorcli-release.json.sig"
if [ -n "${METEORCLI_RELEASE_SIGNING_KEY:-}" ]; then
	go run ./tools/release sign "$out" "$version"
else
	echo "warning: METEORCLI_RELEASE_SIGNING_KEY is not set; the release is unsigned and 'meteorcli updater' refuses it" >&2
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/packaging"
cp -r packaging/meteorcli.service packaging/meteorcli-helper.service packaging/meteorcli-helper.socket \
	packaging/sysusers.d packaging/tmpfiles.d packaging/libexec packaging/sudoers.d "$stage/packaging/"
tar -C "$stage" --owner=0 --group=0 --numeric-owner -czf "$out/meteorcli-packaging.tar.gz" packaging

# Installers: <script name>:<board name as platform.Detect calls it>.
# install-meteorcli.sh runs on any Linux board with systemd (it picks the
# binary by uname -m); install-rpi.sh also checks that it is on a Raspberry Pi.
for entry in install-meteorcli:generic install-rpi:raspberry-pi; do
	name="${entry%%:*}"
	board="${entry#*:}"
	sed -e "s|@BOARD@|$board|" -e "s|@BASE_URL@|$base|" packaging/install/install.sh >"$out/$name.sh"
	chmod 0755 "$out/$name.sh"
done

# First boot and OS images: the settings service for a card's .meteorenv and
# the scripts that install it or turn a device into an image.
for f in install-meteorcli-firstboot.sh meteorcli-settings.sh meteorcli-settings.service meteorcli-prepare-image.sh; do
	install -m 0644 "packaging/firstboot/$f" "$out/$f"
done
chmod 0755 "$out"/*.sh

(cd "$out" && sha256sum meteorcli-linux-* meteorcli-packaging.tar.gz install-*.sh meteorcli-settings.* \
	meteorcli-prepare-image.sh >SHA256SUMS)
echo "release files in $out:"
ls -l "$out"

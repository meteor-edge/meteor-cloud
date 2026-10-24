#!/usr/bin/env bash
# Download the meteorcli release tarball and run its installer.
# Does not clone the MeteorCloud repository.
#
#   curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | bash
#   curl -fsSL .../install-meteorcli.sh | sudo bash -s -- --with-service
#
# Override: METEORCLI_RELEASE_REPO, METEORCLI_VERSION (latest | 0.1.0 | agent-v0.1.0),
# METEORCLI_DIST_URL (full tarball URL). Extra args go to installcli.sh.
set -euo pipefail

REPO="${METEORCLI_RELEASE_REPO:-meteor-edge/meteor-cloud}"
VERSION="${METEORCLI_VERSION:-latest}"
DIST_URL="${METEORCLI_DIST_URL:-}"

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi
if ! command -v tar >/dev/null 2>&1; then
  echo "error: tar is required" >&2
  exit 1
fi

tag_from_version() {
  local v="$1"
  if [[ "${v}" == latest ]]; then
    echo ""
    return
  fi
  if [[ "${v}" == agent-v* ]]; then
    echo "${v}"
    return
  fi
  echo "agent-v${v#v}"
}

if [[ -z "${DIST_URL}" ]]; then
  if [[ "${VERSION}" == latest ]]; then
    BASE="https://github.com/${REPO}/releases/latest/download"
  else
    BASE="https://github.com/${REPO}/releases/download/$(tag_from_version "${VERSION}")"
  fi
  DIST_URL="${BASE}/meteorcli.tar.gz"
  SUMS_URL="${BASE}/SHA256SUMS"
else
  SUMS_URL=""
fi

WORKDIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "Downloading ${DIST_URL}"
curl -fsSL "${DIST_URL}" -o "${WORKDIR}/meteorcli.tar.gz"

if [[ -n "${SUMS_URL}" ]] && command -v sha256sum >/dev/null 2>&1; then
  if curl -fsSL "${SUMS_URL}" -o "${WORKDIR}/SHA256SUMS"; then
    (
      cd "${WORKDIR}"
      sha256sum -c --ignore-missing SHA256SUMS
    )
  else
    echo "warning: could not download SHA256SUMS; skipping checksum" >&2
  fi
fi

tar -xzf "${WORKDIR}/meteorcli.tar.gz" -C "${WORKDIR}"
INSTALLER="$(find "${WORKDIR}" -path '*/agent/installcli.sh' -type f | head -n 1)"
if [[ -z "${INSTALLER}" || ! -f "${INSTALLER}" ]]; then
  echo "error: tarball did not contain agent/installcli.sh" >&2
  exit 1
fi

exec bash "${INSTALLER}" "$@"

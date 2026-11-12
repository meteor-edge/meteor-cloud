#!/usr/bin/env bash
# Download the meteorcli release tarball and run its installer.
# Does not clone the MeteorCloud repository.
#
#   curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | sudo bash
#     (root: also starts meteorcli.service, which connects once the device is registered)
#   curl -fsSL .../install-meteorcli.sh | sudo bash -s -- --no-service   (CLI only)
#   curl -fsSL .../install-meteorcli.sh | bash   (per-user, no background service)
#
# Local / custom tarball (either form):
#   curl -fsSL http://host:8090/install-meteorcli.sh | sudo bash -s -- http://host:8090/meteorcli-0.3.2.tar.gz
#   curl -fsSL http://host:8090/install-meteorcli.sh | sudo METEORCLI_DIST_URL=http://host:8090/meteorcli.tar.gz bash
#
# Override: METEORCLI_RELEASE_REPO, METEORCLI_VERSION (latest | 0.1.0 | agent-v0.1.0),
# METEORCLI_DIST_URL (full tarball URL). Extra args go to installcli.sh.
set -euo pipefail

REPO="${METEORCLI_RELEASE_REPO:-meteor-edge/meteor-cloud}"
VERSION="${METEORCLI_VERSION:-latest}"
DIST_URL="${METEORCLI_DIST_URL:-}"

# First positional arg may be the tarball URL (common with ``bash -s -- http://…/meteorcli.tar.gz``).
if [[ -z "${DIST_URL}" ]] && { [[ "${1:-}" == http://* ]] || [[ "${1:-}" == https://* ]]; }; then
  DIST_URL="$1"
  shift
fi

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

SUMS_URL=""
if [[ -z "${DIST_URL}" ]]; then
  if [[ "${VERSION}" == latest ]]; then
    BASE="https://github.com/${REPO}/releases/latest/download"
  else
    BASE="https://github.com/${REPO}/releases/download/$(tag_from_version "${VERSION}")"
  fi
  DIST_URL="${BASE}/meteorcli.tar.gz"
  SUMS_URL="${BASE}/SHA256SUMS"
else
  # Same directory as the tarball (works for local ``python -m http.server`` dist/).
  SUMS_URL="${DIST_URL%/*}/SHA256SUMS"
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
      # Custom URLs may point at meteorcli-0.3.2.tar.gz while SHA256SUMS also lists meteorcli.tar.gz.
      local_name="$(basename "${DIST_URL}")"
      if [[ "${local_name}" != "meteorcli.tar.gz" ]] && grep -q " meteorcli.tar.gz\$" SHA256SUMS 2>/dev/null; then
        cp -f meteorcli.tar.gz "${local_name}"
      fi
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

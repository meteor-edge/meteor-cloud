#!/usr/bin/env bash
# Pack meteorcli (agent + runtime) for a GitHub Release. No secrets.
#
#   ./scripts/agent-dist.sh
#   make agent-dist
#
# Writes dist/meteorcli-<version>.tar.gz, dist/meteorcli.tar.gz (stable name
# for .../releases/latest/download/), dist/install-meteorcli.sh, dist/SHA256SUMS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_SRC="${ROOT}/src/device-plane/agent"
RUNTIME_SRC="${ROOT}/src/device-plane/runtime"
INSTALLER_SRC="${ROOT}/scripts/install-meteorcli.sh"
DEST="${AGENT_DIST_DIR:-${ROOT}/dist}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync is required" >&2
  exit 1
fi
if [[ ! -f "${AGENT_SRC}/pyproject.toml" || ! -d "${RUNTIME_SRC}" ]]; then
  echo "error: expected ${AGENT_SRC} and ${RUNTIME_SRC}" >&2
  exit 1
fi
if [[ ! -f "${INSTALLER_SRC}" ]]; then
  echo "error: missing ${INSTALLER_SRC}" >&2
  exit 1
fi

VERSION="$(python3 -c "import tomllib; print(tomllib.load(open('${AGENT_SRC}/pyproject.toml', 'rb'))['project']['version'])")"
if [[ -z "${VERSION}" ]]; then
  echo "error: could not read version from ${AGENT_SRC}/pyproject.toml" >&2
  exit 1
fi

STAGE="${DEST}/meteorcli-${VERSION}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}/agent" "${STAGE}/runtime" "${DEST}"

rsync -a \
  --exclude 'tests/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.venv/' \
  --exclude '*.egg-info/' \
  "${AGENT_SRC}/" "${STAGE}/agent/"

rsync -a \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.ruff_cache/' \
  "${RUNTIME_SRC}/" "${STAGE}/runtime/"

# pip install uses the agent dir; Hatch force-includes ../runtime.
if [[ ! -f "${STAGE}/agent/installcli.sh" || ! -f "${STAGE}/runtime/__init__.py" ]]; then
  echo "error: staged tree is incomplete" >&2
  exit 1
fi

tar -C "${DEST}" -czf "${DEST}/meteorcli-${VERSION}.tar.gz" "meteorcli-${VERSION}"
cp -f "${DEST}/meteorcli-${VERSION}.tar.gz" "${DEST}/meteorcli.tar.gz"
cp -f "${INSTALLER_SRC}" "${DEST}/install-meteorcli.sh"
chmod 0755 "${DEST}/install-meteorcli.sh"

(
  cd "${DEST}"
  sha256sum "meteorcli-${VERSION}.tar.gz" "meteorcli.tar.gz" "install-meteorcli.sh" > SHA256SUMS
)

rm -rf "${STAGE}"

echo "Packed meteorcli ${VERSION}:"
echo "  ${DEST}/meteorcli-${VERSION}.tar.gz"
echo "  ${DEST}/meteorcli.tar.gz"
echo "  ${DEST}/install-meteorcli.sh"
echo "  ${DEST}/SHA256SUMS"
echo
echo "Publish: git tag agent-v${VERSION} && git push origin agent-v${VERSION}"
echo "Or: gh release create agent-v${VERSION} ${DEST}/meteorcli.tar.gz ${DEST}/meteorcli-${VERSION}.tar.gz ${DEST}/install-meteorcli.sh ${DEST}/SHA256SUMS"

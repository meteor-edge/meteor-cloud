#!/usr/bin/env bash
# Deploy to AWS (Terraform + Ansible), run the same MQTT live tests, always destroy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/tests${PYTHONPATH:+:$PYTHONPATH}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
  export PATH="$ROOT/.venv/bin:$PATH"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "==> Terraform validate (no AWS resources)"
make terraform-check

echo "==> Ansible syntax"
make ansible-check

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "Cloud E2E: NOT RUN (AWS credentials are not set)"
  exit 0
fi
if ! command -v terraform >/dev/null || ! command -v ansible-playbook >/dev/null; then
  echo "Cloud E2E: NOT RUN (terraform or ansible-playbook is missing)"
  exit 0
fi

CONFIG="${CONFIG:-$ROOT/infrastructure/installer/edge_installer/config/examples/installation.ci.yaml}"
if grep -q '__INSTALLATION_NAME__' "$CONFIG" 2>/dev/null; then
  echo "Cloud E2E: NOT RUN (CONFIG still has CI placeholders; set CONFIG to a filled installation.yaml)"
  exit 0
fi
INSTALLATION_NAME="${INSTALLATION_NAME:-e2e-test-$(date +%s)}"
export INSTALLATION_NAME

if [[ ! -x "$(command -v edge-installer || true)" ]]; then
  "$PYTHON" -m pip install -e "infrastructure/installer/.[dev]" >/dev/null
fi

applied=0

destroy() {
  if [[ "$applied" -eq 1 ]]; then
    echo "==> Destroying AWS test infrastructure"
    (cd "$ROOT/infrastructure/installer" && edge-installer destroy "$CONFIG" --yes) || true
  fi
}
trap destroy EXIT

echo "==> Terraform plan (no apply)"
(cd "$ROOT/infrastructure/installer" && edge-installer plan "$CONFIG")

echo "==> Terraform apply + Ansible deploy ($INSTALLATION_NAME)"
(cd "$ROOT/infrastructure/installer" && edge-installer apply "$CONFIG")
applied=1

{
  read -r PLATFORM_URL
  read -r _mqtt_host
} < <("$PYTHON" - <<'PY'
import json
import os
import sys
from pathlib import Path

name = os.environ["INSTALLATION_NAME"]
path = Path(".installer-state") / name / "installation.json"
if not path.is_file():
    sys.exit(f"missing {path}")
data = json.loads(path.read_text())
url = data.get("platform_url")
ip = data.get("public_ip") or ""
if not url:
    sys.exit("missing platform_url")
print(url)
print(ip)
PY
)
MQTT_HOST="${MQTT_HOST:-$_mqtt_host}"
MQTT_PORT="${MQTT_PORT:-8883}"
export PLATFORM_URL MQTT_HOST MQTT_PORT
export MQTT_ALLOW_BROKER_RESTART=0

echo "==> HTTPS health"
"$PYTHON" scripts/smoke_test.py "$PLATFORM_URL"

echo "==> MQTT TLS :${MQTT_PORT}"
if "$PYTHON" - <<'PY'
import os
import sys
from mqtt_live.http import wait_mqtt_tls

host = os.environ.get("MQTT_HOST") or ""
ca = os.environ.get("MQTT_CA_FILE") or ""
if not host:
    sys.exit(2)
try:
    wait_mqtt_tls(host, int(os.environ.get("MQTT_PORT", "8883")), ca, timeout_seconds=45)
except Exception:
    sys.exit(2)
PY
then
  echo "==> MQTT live tests against deployed broker"
  "$PYTHON" -m pytest -q tests/mqtt_live
else
  echo "Cloud MQTT: NOT RUN (:${MQTT_PORT} is not reachable; production compose does not deploy EMQX yet)"
fi

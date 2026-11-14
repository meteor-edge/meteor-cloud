#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Repo checkout: script lives in scripts/. On an AWS host Ansible installs it as
# $platform_root/generate-mqtt-certs.sh — ROOT is that directory, not its parent.
if [[ -d "$SCRIPT_DIR/deploy/compose" || -f "$SCRIPT_DIR/pyproject.toml" || -d "$SCRIPT_DIR/compose" ]]; then
  ROOT="$SCRIPT_DIR"
else
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
OUT="${1:-$ROOT/certs}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

ENV_FILE="$ROOT/deploy/compose/.env"
if [[ ! -f "$ENV_FILE" && -f "$ROOT/compose/.env" ]]; then
  ENV_FILE="$ROOT/compose/.env"
fi
if [[ -z "${MQTT_PUBLIC_HOST:-}" && -f "$ENV_FILE" ]]; then
  MQTT_PUBLIC_HOST="$(awk -F= '/^MQTT_PUBLIC_HOST=/{print substr($0, index($0,"=")+1); exit}' "$ENV_FILE")"
  MQTT_PUBLIC_HOST="${MQTT_PUBLIC_HOST%$'\r'}"
  case "$MQTT_PUBLIC_HOST" in
    \"*\") MQTT_PUBLIC_HOST="${MQTT_PUBLIC_HOST:1:-1}" ;;
    \'*\') MQTT_PUBLIC_HOST="${MQTT_PUBLIC_HOST:1:-1}" ;;
  esac
fi

if [[ ! -f "$OUT/ca.key" || ! -f "$OUT/ca.crt" ]]; then
  openssl genrsa -out "$OUT/ca.key" 2048 >/dev/null 2>&1
  openssl req -x509 -new -nodes -key "$OUT/ca.key" -sha256 -days 3650 \
    -out "$OUT/ca.crt" -subj "/CN=MeteorCloud Local MQTT CA" \
    -addext "basicConstraints=critical,CA:true" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1
fi

# Bundled broker names on the Compose network: mqtt-broker (alias), mosquitto, emqx.
SAN="DNS:localhost,DNS:mqtt-broker,DNS:mosquitto,DNS:emqx,IP:127.0.0.1"

add_san() {
  local item="$1"
  case ",${SAN}," in
    *",${item},"*) ;;
    *) SAN="${SAN},${item}" ;;
  esac
}

add_host_san() {
  local host="$1"
  [[ -z "$host" ]] && return 0
  case "$host" in
    localhost|127.0.0.1|emqx|mosquitto|mqtt-broker) return 0 ;;
  esac
  if [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    add_san "IP:${host}"
  else
    add_san "DNS:${host}"
  fi
}

add_host_san "${MQTT_PUBLIC_HOST:-}"

# Comma-separated, e.g. the in-cluster broker Service name for Kubernetes.
IFS=',' read -r -a EXTRA_SANS <<< "${MQTT_EXTRA_SANS:-}"
for extra in "${EXTRA_SANS[@]}"; do
  add_host_san "$extra"
done

# LAN devices verify TLS against this machine's address, not 127.0.0.1.
LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
add_host_san "$LAN_IP"

openssl genrsa -out "$OUT/server.key" 2048 >/dev/null 2>&1
openssl req -new -key "$OUT/server.key" -out "$OUT/server.csr" \
  -subj "/CN=localhost" >/dev/null 2>&1

cat > "$OUT/server.ext" <<EOF
subjectAltName=${SAN}
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF

openssl x509 -req -in "$OUT/server.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" \
  -CAcreateserial -out "$OUT/server.crt" -days 3650 -sha256 \
  -extfile "$OUT/server.ext" >/dev/null 2>&1

# Mosquitto and EMQX run as UID/GID 1000; the official VerneMQ image runs as 10000.
# Mode 0644 lets either bind-mounted process read the key. chmod while we still
# own the file, then chown. Do not chmod after a docker chown — GitHub Actions
# (UID 1001) cannot chmod a UID-1000 file.
chmod 0644 "$OUT/server.key"
chmod 600 "$OUT/ca.key"
chmod 644 "$OUT/ca.crt" "$OUT/server.crt"
# Kubernetes reads the key into a Secret instead of bind-mounting it.
if [[ "${MQTT_CERTS_SKIP_CHOWN:-}" == "1" ]]; then
  :
elif ! chown 1000:1000 "$OUT/server.key" 2>/dev/null; then
  if command -v docker >/dev/null 2>&1; then
    docker run --rm --user 0:0 -v "$OUT:/certs" busybox:1.36 \
      chown 1000:1000 /certs/server.key
  else
    echo "error: could not set $OUT/server.key owner to UID/GID 1000 (required so Mosquitto/EMQX can read the bind-mounted key)." >&2
    echo "error: rerun as uid 1000, as root, or with Docker available to chown the file." >&2
    exit 1
  fi
fi

echo "Wrote MQTT development certificates to $OUT"
echo "  CA:     $OUT/ca.crt"
echo "  Server: $OUT/server.crt (SAN ${SAN})"

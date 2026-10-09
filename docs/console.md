# Operator console

The console is the operator UI in this repository (`console/`). The browser talks **only** to the control-plane API (`VITE_API_BASE_URL`). Product docs links use `VITE_DOCS_BASE_URL` (default points at [meteor-edge.com](https://meteor-edge.com) docs in production).

## Local development

```bash
make install-console
make dev-console    # http://localhost:5173
```

Full stack (API + console + optional MQTT): `make dev`. See [Getting started](getting-started.md) and [Development](development.md).

## Production image

```bash
docker build -f infrastructure/docker/Dockerfile.console \
  --build-arg VITE_API_BASE_URL=https://api.example.com \
  --build-arg VITE_DOCS_BASE_URL=https://meteor-edge.com/docs \
  -t ghcr.io/meteor-edge/meteorcloud-console:0.1.0 .
```

Compose and Helm deploy the console next to the control plane. Point `VITE_API_BASE_URL` at the public API origin operators will use.

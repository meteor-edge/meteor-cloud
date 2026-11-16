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

### Security headers

The console's nginx (`console/nginx.conf`) sends a Content-Security-Policy (scripts only from
the console's own origin; styles also inline and from Google Fonts), `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, a restrictive
`Permissions-Policy`, and HSTS (browsers ignore it over plain HTTP). The API adds
`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and
`Content-Security-Policy: default-src 'none'` to its own responses (not to `/docs` and `/redoc`).
If you add a third-party script or font to the console, extend the CSP in `nginx.conf`.

## Fleet UI

- **Public app addresses** — on a device's page, under its public URLs; a deployed app's address also appears as a link in the device's software list once it is ready. Addresses are suggested (`app.device.org.<domain>`) or a custom `name.<org>.<domain>`, checked for availability as you type. Create from a device page or optionally when deploying to a single device. See [Endpoints](fleet/endpoints.md).
- **Device → Terminal** — browser SSH over WireGuard (`device.shell`). See [Connectivity](fleet/connectivity.md).
- **Device → Logs** — follow and search systemd, Docker, nginx and journal logs over WireGuard (`device.logs`). See [Device logs](fleet/logs.md).

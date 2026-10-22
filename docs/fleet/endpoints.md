# Application endpoints

A public URL for one application on one device, for example
`nginx.appx.meteor-edge.com` → `10.8.0.12:80` and
`dashboard.appx.meteor-edge.com` → `10.8.0.12:8080` on the **same** device.

Hostnames are unique. Enabled endpoints cannot share `(device_id, port)`.
Upstream addresses are **derived** from the device’s WireGuard IP; clients
cannot pick an arbitrary destination.

## API

Requires `endpoint.read`. Create/update/delete need the matching
`endpoint.create|update|delete` permission (device-group scoped).

| Method | Path |
|--------|------|
| `GET` | `/api/v1/organizations/{org}/endpoints` (`device_id` filter) |
| `POST` | `/api/v1/organizations/{org}/endpoints` |
| `GET` | `/api/v1/organizations/{org}/endpoints/{id}` |
| `PATCH` | `/api/v1/organizations/{org}/endpoints/{id}` |
| `DELETE` | `/api/v1/organizations/{org}/endpoints/{id}` |

Create body: `{ "device_id", "hostname", "port", "protocol": "http" }`.
Hostname must be `{label}.appx.{APPS_BASE_DOMAIN}`.

Availability (`unknown` / `ready` / `unhealthy` / `offline`) is separate from
Deploy status. Traefik only routes **enabled** + **ready** endpoints. Manual
endpoints (no workload or Deploy) are `ready` when the device’s WireGuard
session is active, otherwise `offline`.

Optional on Deploy create (exactly one target device):

```json
"endpoint": { "hostname": "nginx.appx.meteor-edge.com", "port": 80 }
```

The URL is not marked `ready` until that workload is in-sync and healthy.

## Reverse proxy

Traefik polls `GET /internal/routing/traefik` (internal token, not public).
No per-app file edit or Traefik restart. Disable or delete an endpoint and the
next poll drops the route.

DNS: one wildcard `*.appx.<APPS_BASE_DOMAIN>` to the gateway. Certificate:
Traefik DNS-01 resolver `letsencrypt-dns`. Credentials via env only.

## Related

- [Device connectivity](connectivity.md)
- [Deployments](deployments.md)

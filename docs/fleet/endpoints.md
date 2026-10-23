# Application endpoints

A public address for one app on one device. Two forms, both under
`APPS_BASE_DOMAIN` (for example `meteor-edge.com`):

| Form | Example | Who picks it |
|------|---------|--------------|
| **Suggested** `<app>.<device>.<org>.<domain>` | `shop.kiosk-12.acme.meteor-edge.com` | the platform, from the app (release), device and organization names; a number is added (`shop-2`) while it is taken |
| **Custom** `<name>.<domain>` | `shop.meteor-edge.com` | the user; first come, first served, checked before saving |

The organization is part of a suggested address, so suggestions never collide
between organizations, and an organization can only use addresses that end in
one of **its own** devices and its own organization name. Custom names are
shared by everyone; reserved names (`www`, `api`, `admin`, `console`, …, plus
`APPS_RESERVED_NAMES`) are refused. Earlier `<name>.appx.<domain>` addresses
keep working.

One address per app port: two apps on the same device need different ports.
Upstream addresses are **derived** from the device’s WireGuard IP; clients
cannot pick an arbitrary destination.

## API

Requires `endpoint.read`. Create/update/delete need the matching
`endpoint.create|update|delete` permission (device-group scoped).

| Method | Path |
|--------|------|
| `GET` | `/api/v1/organizations/{org}/endpoints` (`device_id` filter) |
| `GET` | `/api/v1/organizations/{org}/endpoints/hostname-suggestion?device_id=&app=` |
| `GET` | `/api/v1/organizations/{org}/endpoints/hostname-check?device_id=&hostname=` |
| `POST` | `/api/v1/organizations/{org}/endpoints` |
| `GET` | `/api/v1/organizations/{org}/endpoints/{id}` |
| `PATCH` | `/api/v1/organizations/{org}/endpoints/{id}` |
| `DELETE` | `/api/v1/organizations/{org}/endpoints/{id}` |

`hostname-suggestion` returns `{apps_domain, hostname, available}`.
`hostname-check` returns `{hostname, available, reason, message}` with
`reason` one of `available`, `taken`, `reserved`, `invalid`, `not_yours`
(an `app.device.org` address of another device or organization). Pass
`endpoint_id` when renaming so the endpoint's own address counts as available.

Create body: `{ "device_id", "hostname", "port", "protocol": "http" }`.

Availability (`unknown` / `ready` / `unhealthy` / `offline`) is separate from
Deploy status. Traefik only routes **enabled** + **ready** endpoints. Manual
endpoints (no workload or Deploy) are `ready` when the device’s WireGuard
session is active, otherwise `offline`.

Optional on Deploy create (exactly one target device):

```json
"endpoint": { "port": 80 }                                   // suggested address
"endpoint": { "hostname": "shop.meteor-edge.com", "port": 80 } // custom address
```

The URL is not marked `ready` until that workload is in-sync and healthy.

## Reverse proxy

Traefik polls `GET /internal/routing/traefik` (internal token, not public).
No per-app file edit or Traefik restart. Disable or delete an endpoint and the
next poll drops the route.

DNS: one wildcard `*.<APPS_BASE_DOMAIN>` to the gateway; it answers for every
depth, so it covers both forms. Certificates (Traefik DNS-01, resolver
`letsencrypt-dns`): one wildcard `*.<domain>` for custom names and one
`*.<device>.<org>.<domain>` per device with published apps, requested the
first time a route needs it (a minute or two). Let's Encrypt allows about 50 new
certificates per registered domain per week (renewals excluded); ask for a
higher limit before onboarding more devices than that per week. Credentials via
env only.

## Related

- [Device connectivity](connectivity.md)
- [Deployments](deployments.md)

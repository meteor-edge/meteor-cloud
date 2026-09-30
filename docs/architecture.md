# Architecture

MeteorCloud is a modular self-hosted fleet platform. Application modules do not import each other’s internals. They talk over HTTP, MQTT, and environment configuration.

## Runtime

```text
Browser ──► Console ──► Control plane (FastAPI) ──► PostgreSQL
                              │                         Redis
meteor-agent HTTPS ───────────┤
                              │
meteor-agent MQTT ──► EMQX ───┼── HTTP auth/authorize
                              │
Control plane ── HTTP publish/watch ──► Data plane ── MQTT ──► EMQX
Data plane ── HTTP ingest ────────────► Control plane
```

The public **website** is not in this path. It is a marketing and documentation site with no login to the control plane.

## Modules

| Module | Deployable unit | Compose |
| --- | --- | --- |
| Control plane | Identity, tenancy, devices, audit, MQTT policy, ingest, operator API | `compose/control-plane.yml` |
| Data plane | EMQX client, publish/watch, ingest forward | `compose/data-plane.yml` |
| Console | Operator UI | `compose/console.yml` |
| Website | Landing, about, contact, docs | private `meteor-ui` (not this tree) |
| Device agent | `meteorcli` on the edge device | not a Compose service |

Installer-managed **cloud services** (AWS/GCP) are separate from these application modules:

| Service | AWS | GCP Cloud Run |
|---------|-----|----------------|
| `cloud_app` | EC2 + Docker Compose + Traefik | Cloud Run + Cloud SQL + Memorystore + HTTPS LB |
| `vpn` | WireGuard on the EC2 host | Not supported |

See [Modular services](services.md).

## Control plane

```text
src/control-plane/app/
├── api/rest/         # health
├── ports/            # MQTTGateway, RateLimiter, OTAProvider
├── adapters/         # emqx (via data-plane HTTP), redis, ota=none
├── identity/
├── tenancy/
├── devices/
├── audit/
├── mqtt/             # ACL, credentials, ingest, SSE hub, ping orchestration
├── core/
└── main.py
```

PostgreSQL is the control-plane database. There is no generic `DatabaseProvider`. Vendor systems (EMQX, Redis, future OTA) sit behind ports.

## Data plane

Python FastAPI process. Owns the platform MQTT session. Forwards each inbound payload to `POST {CONTROL_PLANE_URL}/internal/mqtt/ingest`. JSON contract: [`contracts/mqtt-http.md`](../contracts/mqtt-http.md).

Keep **one** data-plane MQTT subscriber until shared subscriptions exist.

## Console and website

- Console: Vite/React in private `meteor-edge/meteor-ui`. `VITE_API_BASE_URL` is the control-plane origin only. `VITE_DOCS_BASE_URL` is the public docs site (default `http://localhost:3000/docs`).
- Website: Next.js in the same private repo. Independent deploy. No control-plane login.

See [Frontends](frontends.md).

## Provider selection

| Port | Implemented | Reserved (fail fast) |
|------|-------------|----------------------|
| Persistence | PostgreSQL | — |
| `RateLimiter` | Redis | — |
| `MQTTGateway` | Data-plane HTTP → EMQX | other broker names |
| Telemetry last-value | PostgreSQL columns on `Device` | `timescale`, `clickhouse` |
| `OTAProvider` | `none` | `mender` and others later |

Kafka, object storage, and Mender SDKs are not in the product.

## Kubernetes

No manifests in this repo. A later split would be Deployments: `control-plane`, `data-plane`, `console`, `website`. Scale API, console, and website independently. Keep the data-plane MQTT consumer at 1 until a consumer group exists.

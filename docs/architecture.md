# Architecture

MeteorCloud is a modular self-hosted fleet platform. Application modules do not import each other’s internals. They talk over HTTP, MQTT, and environment configuration.

## Runtime

```text
Browser ──► Console ──► Control plane (FastAPI) ──► PostgreSQL
                              │                         Redis (optional)
                              │                         Object storage (filesystem or S3)
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
| Control plane | Identity, tenancy, devices, audit, MQTT policy, ingest, operator API | `deploy/compose/control-plane.yml` |
| Data plane | EMQX client, publish/watch, ingest forward | `deploy/compose/data-plane.yml` |
| Console | Operator UI | `deploy/compose/console.yml` |
| Website | Landing, about, contact, docs | private `meteor-ui` (not this tree) |
| Device agent | `meteorcli` on the edge device | not a Compose service |

The modules are deployed in one of two ways, with the same images and settings:

| Path | Where | Files |
|------|-------|-------|
| Docker Compose + Ansible | One VM, EC2 instance, or on-prem server | `deploy/compose/`, `deploy/ansible/` |
| Kubernetes + Helm | An existing cluster (MeteorCloud does not provision or manage clusters) | `deploy/kubernetes/helm/meteorcloud/` |

On AWS, `edge-installer` creates the EC2 host with Terraform and runs the Ansible path. See [Deployment](deployment.md), [Kubernetes](kubernetes.md), and [Modular services](services.md).

## Control plane

```text
src/control-plane/app/
├── api/rest/         # health
├── ports/            # MQTTGateway, RateLimiter, OTAProvider
├── adapters/         # emqx (via data-plane HTTP), redis/memory, s3/filesystem, ota=none
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

- Console: Vite/React in `console/`. `VITE_API_BASE_URL` is the control-plane origin only. `VITE_DOCS_BASE_URL` is the public docs site (default `http://localhost:3000/docs`).
- Website: Next.js in private `meteor-edge/meteor-ui`. Independent deploy. No control-plane login.

See [Frontends](frontends.md).

## Provider selection

| Port | Implemented | Reserved (fail fast) |
|------|-------------|----------------------|
| Persistence | PostgreSQL | — |
| `RateLimiter` | `redis`, `memory` (per process) | — |
| `MQTTGateway` | Data-plane HTTP → EMQX | other broker names |
| Telemetry last-value | PostgreSQL columns on `Device` | `timescale`, `clickhouse` |
| `OTAProvider` | `none` | `mender` and others later |
| `ObjectStorage` | `s3` (AWS S3, MinIO, GCS interop, any S3-compatible API), `filesystem` (local or shared volume) | — |

Artifact binaries (OS images, firmware, …) live in object storage; PostgreSQL keeps only their metadata and storage key. See [Artifacts](fleet/artifacts.md). Kafka and Mender SDKs are not in the product.

## Kubernetes

The Helm chart deploys `backend`, `console`, and, with MQTT enabled, `data-plane` and EMQX as separate Deployments. The API and console scale independently; the data-plane MQTT consumer stays at 1 replica until shared subscriptions exist. See [Kubernetes](kubernetes.md).

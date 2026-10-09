# Architecture

MeteorCloud is modular. Planes do not import each other’s internals; they talk over HTTP, MQTT, and configuration. Product-level overview: [Concepts](concepts.md).

## Three planes

| Plane | Deployable unit | Compose |
| --- | --- | --- |
| Control plane | Identity, tenancy, devices, audit, MQTT policy, ingest, operator API | `deploy/compose/control-plane.yml` |
| Data plane | EMQX client, publish/watch, ingest forward | `deploy/compose/data-plane.yml` |
| Device plane | `meteorcli` on the edge device | not a Compose service |

Operator **console**: Vite/React in `console/` — browser → control-plane API only. See [Console](console.md).

```text
┌─────────────────┐     ┌─────────────────┐     ┌──────────────────────┐
│  Device plane   │     │   Data plane    │     │   Control plane      │
│  (meteorcli)    │     │  (MQTT gateway) │     │  (API + Postgres)    │
└────────┬────────┘     └────────┬────────┘     └──────────┬───────────┘
         │                       │                         │
         │  HTTPS enroll /       │                         │
         │  heartbeat            ├────────────────────────►│
         │────────────────────────────────────────────────►│
         │                       │                         │
         │  MQTT ──► EMQX ──────►│  HTTP ingest ──────────►│
         │◄── commands ◄─ EMQX ◄─│◄── HTTP publish ────────│
```

## Deployment paths

Same images and settings, two installers:

| Path | Where | Files |
|------|-------|-------|
| Docker Compose + Ansible | One VM, EC2, or on-prem server | `deploy/compose/`, `deploy/ansible/` |
| Kubernetes + Helm | An existing cluster (MeteorCloud does not provision clusters) | `deploy/kubernetes/helm/meteorcloud/` |

On AWS, `edge-installer` creates the EC2 host with Terraform and runs the Ansible path. See [Deployment](deployment.md), [Kubernetes](kubernetes.md), and [Services](services.md).

## Control plane

```text
src/control-plane/app/
├── api/rest/         # health
├── ports/            # MQTTGateway, RateLimiter, OTAProvider
├── adapters/         # data-plane HTTP, redis/memory, s3/filesystem, ota=none
├── identity/
├── tenancy/
├── devices/
├── audit/
├── mqtt/             # ACL, credentials, ingest, SSE hub
├── core/
└── main.py
```

PostgreSQL is the control-plane database. Vendor systems (EMQX, Redis, future OTA) sit behind ports — no generic `DatabaseProvider`.

## Data plane

Python FastAPI process. Owns the platform MQTT session. Forwards each inbound payload to `POST {CONTROL_PLANE_URL}/internal/mqtt/ingest`. Contract: [`contracts/mqtt-http.md`](../contracts/mqtt-http.md).

Keep **one** data-plane MQTT subscriber until shared subscriptions exist.

## Provider selection

| Port | Implemented | Reserved (fail fast) |
|------|-------------|----------------------|
| Persistence | PostgreSQL | — |
| `RateLimiter` | `redis`, `memory` | — |
| `MQTTGateway` | Data-plane HTTP → EMQX | other broker names |
| Telemetry last-value | PostgreSQL columns on `Device` | `timescale`, `clickhouse` |
| `OTAProvider` | `none` | `mender` and others later |
| `ObjectStorage` | `s3`, `filesystem` | — |

Artifact binaries live in object storage; PostgreSQL keeps metadata and `storage_key`. See [Artifacts](fleet/artifacts.md).

## Kubernetes

The Helm chart deploys `backend`, `console`, and (with MQTT) `data-plane` and EMQX as separate Deployments. API and console scale independently; the data-plane MQTT consumer stays at 1 replica until shared subscriptions exist. See [Kubernetes](kubernetes.md).

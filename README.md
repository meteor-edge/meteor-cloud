# MeteorCloud

**Self-hosted fleet platform for Linux devices.**

MeteorCloud is the control plane, operator console, MQTT data plane, and device agent for managing edge fleets you own. Enroll devices, organize them by type and group, ship artifacts, run commands over MQTT, and keep operator access multi-tenant and auditable — without sending device data to a third-party SaaS.

Website: [meteor-edge.com](https://meteor-edge.com)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## Why MeteorCloud

| | |
| --- | --- |
| **You host it** | Docker Compose on a VM/EC2, or Helm on a cluster you already run |
| **Multi-tenant** | Organizations, memberships, RBAC, and optional device-group scopes |
| **Device lifecycle** | Registration tokens, device-initiated enrollment, heartbeat, inventory |
| **MQTT-ready** | TLS broker (EMQX), per-device credentials, ACL, ingest, live telemetry |
| **Artifacts** | OS images, firmware, compose bundles — metadata in Postgres, blobs in filesystem or S3 |
| **Replaceable infra** | PostgreSQL required; Redis, MQTT, and object storage selected by config |

## Architecture

MeteorCloud is split into three planes. The **console** is the operator UI; it talks only to the control plane.

| Plane | Path | Role |
| --- | --- | --- |
| **Device plane** | `src/device-plane/agent/` | `meteorcli` on the device: enroll, heartbeat, inventory, MQTT client |
| **Data plane** | `src/data-plane/` | Platform MQTT gateway: subscribe/publish via EMQX, forward ingest to the control plane |
| **Control plane** | `src/control-plane/` | Identity, tenancy, device registry, artifacts, MQTT policy, operator API, persistence |

### Schema

```text
┌─────────────────┐     ┌─────────────────┐     ┌──────────────────────┐
│  Device plane   │     │   Data plane    │     │   Control plane      │
│  (meteorcli)    │     │  (MQTT gateway) │     │  (API + Postgres)    │
└────────┬────────┘     └────────┬────────┘     └──────────┬───────────┘
         │                       │                         │
         │  HTTPS enroll /       │                         │
         │  heartbeat / auth     ├────────────────────────►│
         │────────────────────────────────────────────────►│
         │                       │                         │
         │  MQTT telemetry /     │  HTTP ingest            │
         │  status ──► EMQX ────►│────────────────────────►│
         │                       │                         │
         │◄── MQTT commands ◄────│◄── HTTP publish ────────│
         │         EMQX          │                         │
```

**Device → cloud (typical path)**

1. Agent enrolls and authenticates over **HTTPS** to the control plane.
2. Agent publishes telemetry and status over **MQTT** to EMQX.
3. Data plane receives those messages and **HTTP-forwards** them to the control plane for ingest and storage.
4. Operators issue commands from the console → control plane → data plane → MQTT → agent.

More detail: [docs/architecture.md](docs/architecture.md).

## Quick start (local)

```bash
cp deploy/compose/.env.example deploy/compose/.env   # make dev does this if missing
make dev
make seed      # optional: owner@example.com / dev-password-123
```

| Surface | URL |
| --- | --- |
| Console | http://localhost:5173 |
| Control plane | http://localhost:8000 |
| OpenAPI | http://localhost:8000/docs |
| Data plane health | http://localhost:8081/health |
| MQTT TLS | mqtts://localhost:8883 |
| EMQX dashboard (dev) | http://localhost:18083 |

Choose bundled services with `COMPOSE_PROFILES` in `deploy/compose/.env` (`postgres`, `redis`, `minio`, `mqtt`, `emqx`). Start one module: `make dev-control-plane`, `make dev-data-plane`, `make dev-console`. Stop: `make stop`.

Kubernetes (local k3d + Helm):

```bash
make k8s-up && make k8s-deploy && make k8s-status
```

See [docs/development.md](docs/development.md).

## Deployment

Two independent paths; same images and settings.

| | Docker Compose + Ansible | Kubernetes + Helm |
| --- | --- | --- |
| For | One VM, EC2, or on-prem server | An existing cluster (k3s, GKE, EKS, AKS, …) |
| Files | [`deploy/compose/`](deploy/compose/), [`deploy/ansible/`](deploy/ansible/) | [`deploy/kubernetes/helm/meteorcloud/`](deploy/kubernetes/helm/meteorcloud/) |
| Install | `ansible-playbook playbooks/site.yml` (or `edge-installer apply` on AWS) | `helm upgrade --install` |
| Guide | [docs/deployment.md](docs/deployment.md) | [docs/kubernetes.md](docs/kubernetes.md) |

**MeteorCloud does not provision or manage Kubernetes clusters.** The Helm chart installs into a cluster you already have. Ansible only prepares servers and runs Compose; it never deploys to Kubernetes. Terraform ([`infrastructure/terraform/aws`](infrastructure/terraform/aws/)) creates the AWS EC2 host for the Compose path.

On AWS, `make up` (or `edge-installer apply`) runs Terraform then Ansible. Fresh-instance proof: GitHub Actions → **EC2 smoke test** ([docs/aws-ci.md](docs/aws-ci.md)).

### Dependencies

| Dependency | Status | Options |
| --- | --- | --- |
| PostgreSQL | Required | Bundled container, or external |
| Artifact storage | Required | Filesystem volume, AWS S3, or any S3-compatible API |
| MQTT broker | Optional | Bundled or external EMQX; off by default |
| Redis | Optional | Shared rate limits across API replicas; in-memory otherwise |
| Kafka, ClickHouse | Not used | Not deployed |

The smallest install runs PostgreSQL, the API, the console, and a reverse proxy (Traefik with Compose, your ingress with Kubernetes).

## Repository layout

```text
├── src/
│   ├── control-plane/          # FastAPI (app.*), Dockerfile
│   ├── data-plane/             # MQTT gateway (data_plane.*), Dockerfile
│   └── device-plane/agent/     # meteorcli
├── console/                    # operator UI, Dockerfile (nginx)
├── deploy/
│   ├── compose/                # Docker Compose files, .env.example
│   ├── ansible/                # server preparation + Compose deployment
│   └── kubernetes/helm/        # Helm chart
├── infrastructure/
│   ├── terraform/aws/          # EC2 host for the Compose path
│   └── installer/              # edge-installer (Terraform + Ansible on AWS)
├── contracts/                  # HTTP JSON between planes
├── scripts/                    # smoke tests, certificates, CI helpers
├── docs/
└── Makefile
```

## Documentation

Start with [Getting started](docs/getting-started.md), then the full index: [docs/README.md](docs/README.md).

| Doc | Topic |
| --- | --- |
| [Getting started](docs/getting-started.md) | Local stack + first device |
| [Concepts](docs/concepts.md) | Three planes, orgs, devices |
| [API overview](docs/api.md) | Control-plane HTTP map |
| [meteorcli](docs/meteorcli.md) | Device CLI |
| [Architecture](docs/architecture.md) | Providers, deploy paths |
| [Deployment](docs/deployment.md) · [Kubernetes](docs/kubernetes.md) | Production install |

Website: [meteor-edge.com](https://meteor-edge.com).

## License

Copyright © Meteor Edge contributors.

Licensed under the [Apache License, Version 2.0](LICENSE).

```text
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

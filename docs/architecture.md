# Architecture Overview

## Goals

The Edge Platform is a self-hosted Linux control plane with a standalone installer, AWS EC2 or GCP Cloud Run deployment, and a FastAPI + React application stack.

## High-level components

```text
┌────────────────────┐
│  edge-installer    │  Install / maintain services (AWS or GCP)
│  (standalone CLI)  │
└─────────┬──────────┘
          │
     ┌────┴────┐
     ▼         ▼
 AWS EC2     GCP Cloud Run
 Terraform   Terraform only
 + Ansible   (Cloud SQL, Redis, LB)
          │
          ▼
┌──────────────────────────────────────────┐
│              Edge Platform               │
│  ┌──────────────┐     ┌───────────────┐  │
│  │   Frontend   │────▶│    Backend    │  │
│  │ React / Vite │     │ FastAPI / PG  │  │
│  └──────────────┘     └───────────────┘  │
└──────────────────────────────────────────┘
```

The platform application never knows how it was installed. The installer is a separate package that deploys it as one of several optional **services**.

## Modular services

| Service | AWS | GCP Cloud Run |
|---------|-----|----------------|
| `cloud_app` | EC2 + Docker Compose + Traefik | Cloud Run + Cloud SQL + Memorystore + HTTPS LB |
| `vpn` | WireGuard on the EC2 host | Not supported |

Configured in `installation.yaml` under `services:`. Default: both enabled. One command deploys all enabled services: `make up` / `edge-installer apply`.

See [Modular services](services.md).

## Installer architecture

```text
edge-installer
    |
    +-- Configuration (installation.yaml)
    |
    +-- Service registry (cloud_app, vpn, ...)
    |
    +-- AWS provider (EC2 Terraform + Ansible)
    +-- GCP provider (Cloud Run Terraform)

    +-- State (.installer-state/)
    |
    +-- Health verification
```

## Backend structure

```text
control-plane/app/
├── api/rest/         # health
├── ports/            # MQTTGateway, RateLimiter, OTAProvider
├── adapters/         # provider selection (emqx, redis, ota=none)
├── identity/
├── tenancy/
├── devices/
├── audit/
├── core/             # config, db, logging, security
└── main.py

data-plane/data_plane/connectivity/mqtt/   # EMQX adapter
```

## Frontend structure

```text
frontend/src/
├── components/
├── layouts/
├── pages/
├── lib/
└── App.tsx
```

## Repository layout

```text
├── control-plane/
├── data-plane/
├── device-plane/agent/
├── frontend/
├── infrastructure/     # Terraform, Ansible, installer, Docker
├── tests/mqtt_live/
├── docs/
└── Makefile
```

## Milestone scope

| Milestone | Delivered |
|-----------|-----------|
| **1** | Compose dev stack, FastAPI/React foundation, installer scaffold |
| **2** | Auth, organizations, RBAC, frontend org pages |
| **3** | AWS EC2 deploy, modular Terraform/Ansible, cloud_app + vpn services |

## Provider boundaries

Domain code depends on ports in `control-plane/app/ports/`. Current adapters are selected from configuration (`DATABASE_PROVIDER`, `CACHE_PROVIDER`, `MQTT_PROVIDER`, `OTA_PROVIDER`).

| Port | Current adapter | Notes |
|------|-----------------|-------|
| Domain repositories (`DeviceRepository`, …) | PostgreSQL / SQLAlchemy | No generic `DatabaseProvider` |
| `RateLimiter` | Redis (`RedisRateLimiter`) | Tests use `InMemoryRateLimiter` |
| `MQTTGateway` | EMQX (`PlatformMqttClient`) | MQTT HTTP auth stays in `data_plane` |
| `OTAProvider` | none | No OTA product yet |

Future stores (ClickHouse, Kafka, S3/MinIO, Mender) have empty directories only.

Kubernetes and WireGuard stay in `infrastructure/`; they are not imported by domain modules.

## Explicit non-goals (current)

- OTA / Mender, Kafka, ClickHouse, object-storage adapters
- Kubernetes as application logic
- Multi-node, RDS, ElastiCache
- Zero-downtime upgrades

## Extension points

1. New service: Terraform module + Ansible playbook + `services/registry.py` + YAML config
2. Business modules: `control-plane/app/` plus replaceable ports in `app/ports/`
3. New vendor: implement the existing port; select it from settings — do not import the vendor SDK from domain services
4. Remote Terraform state (S3 backend) — designed for, not implemented yet

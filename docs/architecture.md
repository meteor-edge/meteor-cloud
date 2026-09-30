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
platform/backend/
├── app/
│   ├── api/          # health
│   ├── cli/          # create-admin
│   ├── core/         # config, db, logging, security
│   ├── modules/
│   │   ├── identity/       # auth, users
│   │   └── organizations/  # orgs, memberships, RBAC
│   └── main.py
├── alembic/
└── tests/
```

## Frontend structure

```text
platform/frontend/src/
├── components/
├── layouts/
├── pages/            # auth, organizations, dashboard
├── lib/
└── App.tsx
```

## Repository layout

```text
├── installer/          # edge-installer CLI
├── platform/           # backend + frontend
├── infrastructure/     # Terraform modules + Ansible playbooks
├── docs/
├── installation.yaml   # AWS deploy config (local, gitignored state)
└── Makefile            # make dev, make up, make down, ...
```

## Milestone scope

| Milestone | Delivered |
|-----------|-----------|
| **1** | Compose dev stack, FastAPI/React foundation, installer scaffold |
| **2** | Auth, organizations, RBAC, frontend org pages |
| **3** | AWS EC2 deploy, modular Terraform/Ansible, cloud_app + vpn services |

## Explicit non-goals (current)

- Device management, MQTT, OTA
- Kubernetes, multi-node, RDS, ElastiCache
- GCP / Azure
- Zero-downtime upgrades

## Extension points

1. New service: Terraform module + Ansible playbook + `services/registry.py` + YAML config
2. Business modules: `platform/backend/app/modules/`
3. Remote Terraform state (S3 backend) — designed for, not implemented yet

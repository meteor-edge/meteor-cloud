# Edge Platform

Self-hosted Linux Edge Platform — control plane with modular AWS EC2 or GCP Cloud Run deployment.

## What you get

| Area | Purpose |
| --- | --- |
| `control-plane/` | FastAPI domain (identity, tenancy, devices, audit) |
| `data-plane/` | MQTT connectivity and future ingestion/messaging |
| `device-plane/` | On-device agent (`meteorcli` / `edge-agent`) |
| `frontend/` | React operator UI |
| `infrastructure/` | Terraform, Ansible, installer, Docker, observability |
| `docs/` | Architecture, deployment, and development guides |

## Local development

```bash
cp .env.example .env
make dev
make seed      # optional: owner@example.com / dev-password-123
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000/health
- API docs: http://localhost:8000/docs
- MQTT TLS: mqtts://localhost:8883
- EMQX dashboard (dev): http://localhost:18083

Stop: `make stop`

## Cloud deployment

**AWS (EC2 + Ansible)** — see [AWS deployment](docs/aws-deployment.md):

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
# Edit installation.yaml — provider: aws
make up
```

**GCP (Cloud Run)** — see [GCP Cloud Run](docs/gcp-deployment.md):

```bash
cp installer/edge_installer/config/examples/installation.gcp.yaml ./installation.yaml
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
make up
```

## Tooling

```bash
source .venv/bin/activate
make install
make test
make lint
make terraform-check
make ansible-check
```

## Repository layout

```text
├── control-plane/              # FastAPI app (app.*)
├── data-plane/                 # MQTT adapter (data_plane.*)
├── device-plane/agent/         # edge-agent / meteorcli
├── frontend/                   # React
├── infrastructure/
│   ├── terraform/
│   ├── ansible/
│   ├── installer/              # edge-installer CLI
│   ├── docker/
│   └── observability/
├── tests/mqtt_live/
├── docs/
└── Makefile
```

## Documentation

| Topic | Doc |
|-------|-----|
| **Quick install** | [docs/install-quickstart.md](docs/install-quickstart.md) |
| **Modular services** | [docs/services.md](docs/services.md) |
| **Configuration** | [docs/installer-configuration.md](docs/installer-configuration.md) |
| **GCP Cloud Run** | [docs/gcp-deployment.md](docs/gcp-deployment.md) |
| **AWS prerequisites** | [docs/aws-prerequisites.md](docs/aws-prerequisites.md) |
| **AWS deployment** | [docs/aws-deployment.md](docs/aws-deployment.md) |
| **AWS CI (throwaway EC2)** | [docs/aws-ci.md](docs/aws-ci.md) |
| **Upgrades** | [docs/upgrades.md](docs/upgrades.md) |
| **Destroy** | [docs/destroy.md](docs/destroy.md) |
| **Troubleshooting** | [docs/troubleshooting.md](docs/troubleshooting.md) |
| **Observability** | [docs/observability.md](docs/observability.md) |
| **Architecture** | [docs/architecture.md](docs/architecture.md) |
| **Development** | [docs/development.md](docs/development.md) |
| **Auth & orgs** | [docs/identity-and-organizations.md](docs/identity-and-organizations.md) |
| **Fleet: device types & groups** | [docs/fleet/device-types.md](docs/fleet/device-types.md) |
| **Fleet: registration tokens** | [docs/fleet/registration-tokens.md](docs/fleet/registration-tokens.md) |
| **Fleet: device registration** | [docs/fleet/device-registration.md](docs/fleet/device-registration.md) |
| **Fleet: API keys** | [docs/fleet/enrollment-api-keys.md](docs/fleet/enrollment-api-keys.md) |
| **Fleet: device-initiated enrollment** | [docs/fleet/device-request-enrollment.md](docs/fleet/device-request-enrollment.md) |
| **Fleet: device authentication** | [docs/fleet/device-authentication.md](docs/fleet/device-authentication.md) |
| **Fleet: heartbeat & status** | [docs/fleet/heartbeat.md](docs/fleet/heartbeat.md) |
| **Device agent (edge-agent / meteorcli)** | [device-plane/agent/README.md](device-plane/agent/README.md) |
| **Infrastructure** | [infrastructure/README.md](infrastructure/README.md) |
| **Installer** | [infrastructure/installer/README.md](infrastructure/installer/README.md) |

## Milestone status

- **Milestone 1** — dev stack, FastAPI/React foundation, installer CLI
- **Milestone 2** — auth, organizations, memberships, RBAC
- **Milestone 3** — modular AWS deploy (`cloud_app`, `vpn`), `make up`
- **Milestone 4** — fleet foundation: device types/groups, registration tokens, device registration & heartbeat, reference agent

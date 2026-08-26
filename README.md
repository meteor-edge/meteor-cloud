# Edge Platform

Self-hosted Linux Edge Platform — control plane with modular AWS deployment.

## What you get

| Area | Purpose |
| --- | --- |
| `installer/` | `edge-installer` CLI — Terraform + Ansible deploy |
| `platform/` | FastAPI backend + React frontend |
| `infrastructure/` | Modular Terraform modules + Ansible playbooks |
| `docs/` | Architecture, deployment, and development guides |
| `installation.yaml` | AWS deploy configuration (copy from example) |

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

## AWS deployment (one command)

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'

# Edit installation.yaml — enable services under services:
make up        # Terraform + Ansible for all enabled services
make plan      # preview
make status-aws
make down      # destroy
```

Default services: **cloud_app** (platform app) + **vpn** (WireGuard). Toggle in `installation.yaml`:

```yaml
services:
  cloud_app:
    enabled: true
  vpn:
    enabled: false
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
├── installer/edge_installer/   # CLI, config, service registry
├── platform/backend/           # FastAPI
├── platform/frontend/          # React
├── infrastructure/
│   ├── terraform/modules/      # cloud_app, vpn, ...
│   └── ansible/playbooks/      # site.yml, services/
├── docs/
├── installation.yaml
└── Makefile
```

## Documentation

| Topic | Doc |
|-------|-----|
| **Quick install** | [docs/install-quickstart.md](docs/install-quickstart.md) |
| **Modular services** | [docs/services.md](docs/services.md) |
| **Configuration** | [docs/installer-configuration.md](docs/installer-configuration.md) |
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
| **Device agent (edge-agent / meteorcli)** | [edge-agent/README.md](edge-agent/README.md) |
| **Infrastructure** | [infrastructure/README.md](infrastructure/README.md) |
| **Installer** | [installer/README.md](installer/README.md) |

## Milestone status

- **Milestone 1** — dev stack, FastAPI/React foundation, installer CLI
- **Milestone 2** — auth, organizations, memberships, RBAC
- **Milestone 3** — modular AWS deploy (`cloud_app`, `vpn`), `make up`
- **Milestone 4** — fleet foundation: device types/groups, registration tokens, device registration & heartbeat, reference agent

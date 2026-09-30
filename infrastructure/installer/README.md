# Edge Platform Installer

Standalone CLI (`edge-installer`) to install and maintain the Edge Platform on AWS EC2 or GCP Cloud Run. The platform application does not know how it was installed.

## Quick start

```bash
cd infrastructure/installer
pip install -e ".[dev]"

# From repo root — copy and edit config first
edge-installer init -o ../../installation.yaml

export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'

edge-installer validate ../../installation.yaml
make -C ../.. up          # or: edge-installer apply ../../installation.yaml
```

## Commands

| Command | Description |
|---------|-------------|
| `edge-installer init` | Create sample `installation.yaml` |
| `edge-installer validate [config]` | Check config, secrets, tools, AWS creds |
| `edge-installer plan [config]` | Terraform plan (no changes) |
| `edge-installer apply [config]` | Terraform + Ansible + health check |
| `edge-installer status [config]` | State, infrastructure, health |
| `edge-installer upgrade [config]` | Update enabled services on existing host |
| `edge-installer destroy [config]` | Tear down AWS + local state |

Config path can be positional (`installation.yaml`) or `--config installation.yaml`.

Makefile equivalents from repo root: `make up`, `make down`, `make plan`, `make status-aws`.

## Modular services

Configure which stacks to deploy in `installation.yaml`:

```yaml
services:
  cloud_app:
    enabled: true
  vpn:
    enabled: true
```

Service definitions live in `edge_installer/services/registry.py`. Each service maps to Terraform modules and Ansible playbooks under `infrastructure/`.

See [Modular services](../../docs/services.md).

## Package layout

```text
infrastructure/installer/edge_installer/
├── cli/              # Typer CLI
├── config/           # YAML loading and validation
├── services/         # Service registry (cloud_app, vpn, ...)
├── providers/aws/    # EC2 Terraform
├── providers/gcp/    # Cloud Run Terraform
├── deployment/       # Ansible, SSH, orchestration
├── state/            # Local state and locking
├── health/           # Post-deploy checks
└── process/          # Subprocess runner
```

## Configuration

Example: `edge_installer/config/examples/installation.yaml`

Documentation: [Installer configuration](../../docs/installer-configuration.md)

## State

```text
.installer-state/<installation-name>/
├── installation.json
├── inventory.ini
├── extra-vars.json
├── install.lock
└── terraform/
    ├── modules/          # copied at apply time
    └── terraform.tfstate
```

Never commit `.installer-state/` or secrets.

## Development

```bash
cd infrastructure/installer
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
```

## Documentation

- [Install quickstart](../../docs/install-quickstart.md)
- [AWS prerequisites](../../docs/aws-prerequisites.md)
- [Troubleshooting](../../docs/troubleshooting.md)

# Modular services

Deploy one or more independent stacks from a single `installation.yaml` and one command.

## Available services

| Service | Description | Requires |
|---------|-------------|----------|
| `cloud_app` | Edge Platform (API, frontend, Postgres, Redis) | AWS: Docker/Traefik. GCP: Cloud Run |
| `vpn` | WireGuard VPN tunnel on the EC2 host | AWS only; disable on GCP |

More services can be added following the extension guide below.

## Configuration

```yaml
services:
  cloud_app:
    enabled: true
  vpn:
    enabled: true
    listen_port: 51820
    network_cidr: 10.8.0.0/24
    allowed_client_cidrs:
      - 0.0.0.0/0
    server_address: 10.8.0.1/24
```

**Default:** both enabled.

**Disable VPN only:**

```yaml
services:
  cloud_app:
    enabled: true
  vpn:
    enabled: false
```

When `cloud_app` is disabled, Postgres/JWT secrets are not required. VPN alone is not supported (shared host).

## Deploy

```bash
make up
```

The installer:

1. Passes `enabled_services` to Terraform (creates only needed AWS resources)
2. Runs Ansible `site.yml` (provisions shared host, deploys each enabled service)

## Per-service responsibilities

### cloud_app

| Layer | Location |
|-------|----------|
| Terraform | `infrastructure/terraform/modules/cloud_app/` |
| Ansible | `playbooks/services/cloud_app.yml` |
| Config | `components:`, `deployment:`, `platform:` |

Secrets: `EDGE_PLATFORM_POSTGRES_PASSWORD`, `EDGE_PLATFORM_JWT_SECRET`

### vpn

| Layer | Location |
|-------|----------|
| Terraform | `infrastructure/terraform/modules/vpn/` (UDP SG rule) |
| Ansible | `playbooks/services/vpn.yml`, `roles/vpn/` |
| Config | `services.vpn.*` |

Secret: `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY` (optional — installs packages without it)

## Directory layout

```text
infrastructure/
├── terraform/
│   ├── aws/                    # root stack
│   └── modules/
│       ├── cloud_app/
│       └── vpn/
└── ansible/
    ├── playbooks/
    │   ├── site.yml
    │   ├── provision.yml
    │   ├── deploy.yml
    │   └── services/
    │       ├── cloud_app.yml
    │       └── vpn.yml
    └── roles/
        ├── platform_*/
        └── vpn/

infrastructure/installer/edge_installer/services/
└── registry.py                 # service definitions
```

## Adding a new service

1. **Terraform** — create `infrastructure/terraform/modules/<name>/`
2. **Wire root stack** — add module block in `terraform/aws/main.tf` gated by `enabled_services`
3. **Ansible** — create `playbooks/services/<name>.yml` and role(s)
4. **Import** — add to `playbooks/deploy.yml`:
   ```yaml
   - import_playbook: services/<name>.yml
     when: "'<name>' in enabled_services"
   ```
5. **Registry** — add entry in `infrastructure/installer/edge_installer/services/registry.py`
6. **Config** — add settings under `services:` in `installation.yaml` and Pydantic models

## Related

- [Install quickstart](install-quickstart.md)
- [Installer configuration](installer-configuration.md)
- [Architecture](architecture.md)

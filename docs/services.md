# Modular services

`edge-installer` deploys one or more independent stacks to an AWS EC2 host from a single `installation.yaml`. It runs Terraform for the host and the Ansible playbooks in `deploy/ansible/` for the services. For Kubernetes use the Helm chart instead ([Kubernetes](kubernetes.md)).

## Available services

| Service | Description | Requires |
|---------|-------------|----------|
| `cloud_app` | Application stack via Docker Compose (API, console, PostgreSQL, Traefik; optional Redis, MQTT) | Docker on the host |
| `vpn` | WireGuard device gateway on the API host (or a dedicated AWS/on-prem server; see [Connectivity](fleet/connectivity.md)) | `cloud_app` (same host) |

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
make aws-up
```

The installer:

1. Passes `enabled_services` to Terraform (creates only needed AWS resources)
2. Runs Ansible `site.yml` (provisions shared host, deploys each enabled service)

## Per-service responsibilities

### cloud_app

| Layer | Location |
|-------|----------|
| Terraform | `infrastructure/terraform/aws/modules/cloud_app/` |
| Ansible | `deploy/ansible/playbooks/services/cloud_app.yml` (runs `deploy/compose/`) |
| Config | `components:`, `deployment:`, `platform:` |

Secrets: `EDGE_PLATFORM_POSTGRES_PASSWORD`, `EDGE_PLATFORM_JWT_SECRET`

### vpn

| Layer | Location |
|-------|----------|
| Terraform | `infrastructure/terraform/aws/modules/vpn/` (UDP rule, `same_host`), `modules/vpn_gateway/` (EC2 + SG + EIP, `dedicated`) |
| Ansible | `deploy/ansible/playbooks/services/vpn.yml`, `roles/vpn/`; `playbooks/wireguard.yml` for a gateway alone |
| Config | `services.vpn.*` |

Secrets: `EDGE_PLATFORM_CONNECTIVITY_INTERNAL_TOKEN` (or the MQTT internal token); `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY` is optional, a key is generated on the gateway without it

## Directory layout

```text
infrastructure/terraform/aws/
├── main.tf                     # root stack, gated by enabled_services
└── modules/
    ├── cloud_app/
    └── vpn/
deploy/ansible/
├── playbooks/
│   ├── site.yml
│   ├── provision.yml
│   ├── deploy.yml
│   └── services/
│       ├── cloud_app.yml
│       └── vpn.yml
└── roles/
    ├── meteorcloud/            # all settings and defaults
    ├── platform_*/
    └── vpn/
infrastructure/installer/edge_installer/services/
└── registry.py                 # service definitions
```

## Adding a new service

1. **Terraform** — create `infrastructure/terraform/aws/modules/<name>/`
2. **Wire root stack** — add module block in `terraform/aws/main.tf` gated by `enabled_services`
3. **Ansible** — create `deploy/ansible/playbooks/services/<name>.yml` and role(s)
4. **Import** — add to `deploy/ansible/playbooks/deploy.yml`:
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

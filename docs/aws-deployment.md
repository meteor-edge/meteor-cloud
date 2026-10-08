# AWS deployment

Deploy modular services to a single AWS EC2 instance with one command.

## Quick start

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'

# Edit installation.yaml — services, region, SSH key, etc.
make up
```

Equivalent: `edge-installer apply installation.yaml`

## Configure services

```yaml
services:
  cloud_app:
    enabled: true
  vpn:
    enabled: true
    listen_port: 51820
    allowed_client_cidrs:
      - 0.0.0.0/0
```

See [Modular services](services.md) for details and adding new services.

## Application source (cloud_app)

By default the installer clones and builds from:

```text
https://github.com/shahabkamali/MeteorCloud.git
```

```yaml
deployment:
  repository_url: https://github.com/shahabkamali/MeteorCloud.git
  git_ref: master
  image_source: git
  backend_image: edge-platform-backend:0.2.0
  frontend_image: edge-platform-frontend:0.2.0
  image_pull_policy: never
```

Registry-based deploy:

```yaml
deployment:
  image_source: registry
  backend_image: ghcr.io/example/edge-platform-backend:0.2.0
  frontend_image: ghcr.io/example/edge-platform-frontend:0.2.0
  image_pull_policy: always
```

Push app changes to GitHub before deploy when using `image_source: git`.

## Workflow

```bash
edge-installer validate installation.yaml
make plan                              # or edge-installer plan
make up                                # or edge-installer apply
make status-aws                        # or edge-installer status
edge-installer upgrade installation.yaml
make down                              # or edge-installer destroy --yes
```

## What `apply` / `make up` does

1. Validates configuration and secrets
2. Runs Terraform for **enabled services** (EC2, SG, EIP, VPN rules)
3. Waits for SSH
4. Ansible `site.yml`:
   - **provision** — Docker, directories (shared)
   - **deploy** — each enabled service playbook
5. For `cloud_app`: copy `deploy/compose/` to `/opt/edge-platform/compose`, render `.env`, clone the repo and build images (or pull from a registry), `docker compose up --wait` (the API runs migrations on start), optional first admin
6. For `vpn`: install WireGuard (active when `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY` is set)
7. Health check (cloud_app only)

## Expected success output

```text
Installation completed successfully.

Installation: production
Services: cloud_app, vpn
Provider: AWS
Region: eu-central-1
Instance ID: i-0123456789abcdef0
Public IP: 18.198.10.20
Platform URL: http://18.198.10.20
```

## Network layout (cloud_app)

```text
Internet -> Traefik -> /api, /health -> Backend
                     -> /           -> Console
PostgreSQL (and Redis, if enabled): internal Docker network only
Artifacts: /opt/edge-platform/data/artifacts (filesystem storage)
VPN (optional): UDP 51820 -> WireGuard on host
```

## Limitations

- Single EC2 instance (for clusters use the [Helm chart](kubernetes.md))
- MQTT is off with the installer defaults; enable it by running the playbooks with your own inventory ([Deployment](deployment.md))
- Local Terraform state
- VPN shares the cloud_app host
- No automatic DNS management
- No zero-downtime upgrades
- Git builds on EC2 (slower than registry pulls)

## Related docs

- [Install quickstart](install-quickstart.md)
- [AWS prerequisites](aws-prerequisites.md)
- [Installer configuration](installer-configuration.md)
- [Troubleshooting](troubleshooting.md)

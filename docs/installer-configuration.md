# Installer configuration

Single YAML file, typically `installation.yaml` at the repo root.

## Sections

| Section | Purpose |
|---------|---------|
| `installation` | Name, environment, provider (`aws`) |
| `platform` | App version, optional domain / public URL |
| `aws` | Region, instance type, SSH key, Elastic IP |
| `network` | SSH CIDRs, HTTP/HTTPS exposure |
| `services` | **Which stacks to deploy** (cloud_app, vpn, ...) |
| `components` | In-app components when cloud_app is enabled (Postgres, Redis, Traefik) |
| `deployment` | Git repo / container images, health timeout |
| `secrets` | Must be `environment` — values from env vars |

Full example: `infrastructure/installer/edge_installer/config/examples/installation.yaml`

## Services (modular deploy)

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

- Default: both enabled
- `vpn` requires `cloud_app` (same EC2 host)
- At least one service must be enabled

When `cloud_app` is disabled, Postgres/JWT secrets are not required.

See [Modular services](services.md).

## AWS settings

```yaml
aws:
  region: eu-central-1
  instance_type: t3.small
  architecture: amd64
  ssh_key_name: edge-platform
  ssh_private_key_path: ~/.ssh/edge-platform.pem
  root_volume_size_gb: 30
  assign_elastic_ip: true
  profile: null   # optional AWS profile name
```

## Network

```yaml
network:
  allowed_ssh_cidrs:
    - 203.0.113.10/32
  allow_http: true
  allow_https: true
```

## Deployment source (cloud_app)

```yaml
deployment:
  repository_url: https://github.com/shahabkamali/MeteorCloud.git
  git_ref: master
  image_source: git
  backend_image: edge-platform-backend:0.2.0
  frontend_image: edge-platform-frontend:0.2.0
  image_pull_policy: never
  health_check_timeout_seconds: 180
```

## URL behavior

- No domain: `http://<public-ip>`
- With domain: `https://<domain>` when HTTPS enabled
- `platform.public_url` overrides auto-detection

DNS must point to the server before Let's Encrypt can issue certificates.

## Required environment variables

When `cloud_app` is enabled:

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'
```

Optional:

```bash
export EDGE_PLATFORM_ADMIN_EMAIL='admin@example.com'
export EDGE_PLATFORM_ADMIN_PASSWORD='...'
export EDGE_PLATFORM_ACME_EMAIL='ops@example.com'      # HTTPS + domain
export EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY='...'    # activate WireGuard
export EDGE_PLATFORM_REDIS_PASSWORD='...'
```

Never put secrets in `installation.yaml` or commit them.

## State files

```text
.installer-state/<installation-name>/
├── installation.json
├── inventory.ini
├── extra-vars.json
└── terraform/
```

Gitignored. Do not commit.

## Commands

```bash
edge-installer validate installation.yaml
make up      # apply
make plan
make down    # destroy
```

See [Install quickstart](install-quickstart.md).

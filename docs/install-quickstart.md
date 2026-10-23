# Install the platform (short guide)

| Goal | Where | Command | Stop |
| --- | --- | --- | --- |
| Testing / development | Your laptop | `make dev` then `make seed` | `make stop` |
| Production | The VM that will host it | `make up` (on that host) | `make down` |
| Production, new AWS EC2 | Your laptop | `make aws-up` | `make aws-down` |
| Production, existing cluster | Your cluster | Helm | `helm uninstall` |

Local details: [Getting started](getting-started.md) · [Development](development.md).
Production on a VM is this page. New EC2: [AWS](#create-a-new-aws-ec2-from-a-laptop). Kubernetes: [Kubernetes](kubernetes.md).

## Testing and development (laptop)

Docker Compose on **your machine**. Not Ansible, not Traefik, not WireGuard.

```bash
git clone https://github.com/meteor-edge/meteor-cloud.git
cd meteor-cloud
make dev
make seed      # owner@example.com / dev-password-123
```

Console: http://localhost:5173 · API: http://localhost:8000 · stop: `make stop`.

## Production on this VM (on-prem or an existing server)

Linux host with sudo: **Ubuntu 22.04+**, **Debian 12+**, or **RHEL / CentOS / Rocky / AlmaLinux 9+**.
The app runs in Docker, so it does not depend on the host distro. `make up` runs
**Ansible** on this machine: it installs Docker (and WireGuard) with the packages
for that OS, then starts Compose.

`PLATFORM_DOMAIN` is required (the script exits if it is missing):

```bash
export PLATFORM_DOMAIN=fleet.example.com
make up
# or: make up PLATFORM_DOMAIN=fleet.example.com
```

Then a **checkbox** of optional services (all on). **Up/down** to move, **space**
to check or uncheck, **Enter** to continue.

```text
  > [x]  WireGuard (vpn)
         What: a private tunnel devices dial.
         For:  browser SSH and app endpoints, without public SSH.

    [x]  MQTT (data plane)
         What: a TLS broker for device messages.
         For:  live telemetry and commands.

    [x]  Artifact storage
         What: where OS images, firmware, and compose bundles live.
         For:  operators upload in the console; devices download via the API.
```

It then prints DNS records for that setup. `--storage=s3` or `--mqtt=external`
still ask for bucket or broker details before install. WireGuard also turns on
browser SSH (Terminal on the device page).

Clone **on the host**:

```bash
# Ubuntu / Debian:
sudo apt-get update && sudo apt-get install -y git make
# RHEL / CentOS / Rocky / Alma:
# sudo dnf install -y git make

git clone https://github.com/meteor-edge/meteor-cloud.git
cd meteor-cloud
make up PLATFORM_DOMAIN=fleet.example.com
```

Create the printed A records (plain DNS, no proxy), then open
**https://fleet.example.com**. Storage is never a public hostname; downloads go
through the API. Bundled MQTT uses `mqtt.fleet.example.com`. Full list:
[DNS records](dns.md).

Non-interactive (CI or a script):

```bash
METEORCLOUD_NONINTERACTIVE=1 make up PLATFORM_DOMAIN=fleet.example.com \
  ARGS='--vpn --storage=filesystem --mqtt=off'
```

Ansible is installed into `~/.meteorcloud-ansible` if it is missing. Secrets are
generated for you (Postgres, JWT, admin login, WireGuard token). After install
they are printed so you can copy them, and saved mode `0600` at
`~/.meteorcloud/secrets.env` (your copy, outside the repo) and
`deploy/ansible/inventory/selfhost/secrets.env` (used by `make up` / `make down`,
gitignored). Re-run `make up` to upgrade; the same secrets are reused.

| Command | What it does |
| --- | --- |
| `make up` | Install (checkbox of services, all on; Enter accepts) |
| `make up ARGS='--no-vpn --mqtt=off --storage=filesystem'` | Same, no prompts for those |
| `make down` | Stop the stack; data stays in `/opt/edge-platform` |
| `make down ARGS=--destroy-data` | Stop and delete `/opt/edge-platform` |

Re-runs remember the domain from `~/.meteorcloud/install.env` if you omit it.
Override the URL with `PLATFORM_PUBLIC_URL=https://fleet.example.com` only if you
need a different scheme or host.

Details: [Deployment](deployment.md) · [DNS records](dns.md).

## Create a new AWS EC2 from a laptop

- Python 3.13+, Terraform 1.7+, ansible-core 2.15+, AWS credentials, SSH key pair
- Copy `infrastructure/installer/edge_installer/config/examples/installation.yaml`
- See [AWS prerequisites](aws-prerequisites.md)

```bash
cp infrastructure/installer/edge_installer/config/examples/installation.yaml ./installation.yaml
# Edit: aws.region, ssh key, allowed_ssh_cidrs, services
```

Enable or disable services (default: all on):

```yaml
services:
  cloud_app:
    enabled: true
  vpn:
    enabled: true
```

Details: [Modular services](services.md)

**Domain and DNS.** Set `platform.domain` (e.g. `fleet.example.com`) for HTTPS,
then create the A records once you know the server's IP (`make aws-up` prints them
at the end; plain DNS, no proxy):

| Service | Record | For |
| --- | --- | --- |
| `cloud_app` | `fleet.example.com` | console, API |
| `cloud_app` (apps) | `*.fleet.example.com` | public app URLs |
| `vpn` | `vpn.fleet.example.com` | WireGuard (UDP 51820) |

Details: [DNS records](dns.md).

### Secrets

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'

# optional
export EDGE_PLATFORM_ADMIN_EMAIL='admin@example.com'
export EDGE_PLATFORM_ADMIN_PASSWORD='...'
export EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY='...'
```

### Deploy

```bash
edge-installer validate installation.yaml
make aws-up
```

| Make | Installer | Action |
|------|-----------|--------|
| `make aws-up` | `edge-installer apply` | Create EC2 and deploy enabled services |
| `make plan` | `edge-installer plan` | Preview Terraform |
| `make status-aws` | `edge-installer status` | Status + health |
| `make aws-down` | `edge-installer destroy --yes` | Tear down AWS |
| — | `edge-installer upgrade` | Update app on existing host |

```text
make aws-up
  → Terraform (modules per enabled service)
  → SSH wait
  → Ansible site.yml
      → provision (Docker, dirs)
      → deploy (cloud_app, vpn, ...)
  → health check (cloud_app)
```

### Verify

```bash
dig +short fleet.example.com vpn.fleet.example.com app.fleet.example.com
curl -sS https://fleet.example.com/api/v1/health
```

## More

- [Installer configuration](installer-configuration.md)
- [AWS deployment](aws-deployment.md)
- [EC2 smoke test](aws-ci.md) (manual GitHub Actions: Terraform + Ansible + destroy)
- [Troubleshooting](troubleshooting.md)

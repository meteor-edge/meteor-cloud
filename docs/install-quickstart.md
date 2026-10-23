# Install the platform (short guide)

| You are… | Command | Guide |
| --- | --- | --- |
| On the VM that will run MeteorCloud | `make up` | this page |
| On a laptop, creating a **new** AWS EC2 | `make aws-up` | [AWS](#create-a-new-aws-ec2-from-a-laptop) |
| On a laptop, developing | `make dev` | [Getting started](getting-started.md) |
| Installing into an existing Kubernetes cluster | Helm | [Kubernetes](kubernetes.md) |

## On this VM (on-prem or an existing server)

Linux host with sudo: **Ubuntu 22.04+**, **Debian 12+**, or **RHEL / CentOS / Rocky / AlmaLinux 9+**.
The app runs in Docker, so it does not depend on the host distro. `make up` runs
**Ansible** on this machine: it installs Docker (and WireGuard) with the packages
for that OS, then starts Compose.

Clone **on the host**:

```bash
# Ubuntu / Debian:
sudo apt-get update && sudo apt-get install -y git make
# RHEL / CentOS / Rocky / Alma:
# sudo dnf install -y git make

git clone https://github.com/meteor-edge/meteor-cloud.git
cd meteor-cloud
make up
```

Ansible is installed into `~/.meteorcloud-ansible` if it is missing. Secrets are
generated for you (Postgres, JWT, admin login, WireGuard token). After install
they are printed so you can copy them, and saved mode `0600` at
`~/.meteorcloud/secrets.env` (your copy, outside the repo) and
`deploy/ansible/inventory/selfhost/secrets.env` (used by `make up` / `make down`,
gitignored). Re-run `make up` to upgrade; the same secrets are reused.

| Command | What it does |
| --- | --- |
| `make up` | Install or upgrade MeteorCloud + WireGuard on this host |
| `make up ARGS=--no-vpn` | Same, without WireGuard |
| `make down` | Stop the stack; data stays in `/opt/edge-platform` |
| `make down ARGS=--destroy-data` | Stop and delete `/opt/edge-platform` |

Optional: `PLATFORM_PUBLIC_URL=https://fleet.example.com make up` if the detected
public URL is wrong (otherwise it uses the instance public IP, or `http://127.0.0.1`).

Details: [Deployment](deployment.md).

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

**Domain and DNS.** Set `platform.domain` (e.g. `meteor-edge.com`) for HTTPS,
then create three A records once you know the server's IP (`make aws-up` prints them
at the end; plain DNS, no proxy):

| Record | Points to | For |
| --- | --- | --- |
| `meteor-edge.com` | server IP | console, API, MQTT |
| `*.meteor-edge.com` | server IP | public app URLs |
| `wg.meteor-edge.com` | server IP, or the dedicated gateway's IP | WireGuard (UDP 51820) |

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
dig +short meteor-edge.com wg.meteor-edge.com app.meteor-edge.com   # all expected IPs
curl http://<public-ip>/api/v1/health
open http://<public-ip>/
```

## More

- [Installer configuration](installer-configuration.md)
- [AWS deployment](aws-deployment.md)
- [EC2 smoke test](aws-ci.md) (manual GitHub Actions: Terraform + Ansible + destroy)
- [Troubleshooting](troubleshooting.md)

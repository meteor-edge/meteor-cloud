# Install the platform (short guide)

Deploy MeteorCloud to an **AWS EC2** host with `edge-installer` (Terraform + Ansible + Docker Compose). For other servers run Ansible directly ([Deployment](deployment.md)); for Kubernetes use Helm ([Kubernetes](kubernetes.md)).

## AWS (EC2)

- Python 3.13+, Terraform 1.5+, ansible-core 2.15+, AWS credentials, SSH key pair
- Copy `infrastructure/installer/edge_installer/config/examples/installation.yaml`
- See [AWS prerequisites](aws-prerequisites.md)

## 2. Configure (AWS)

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

## 3. Secrets

```bash
export EDGE_PLATFORM_POSTGRES_PASSWORD='...'
export EDGE_PLATFORM_JWT_SECRET='...'

# optional
export EDGE_PLATFORM_ADMIN_EMAIL='admin@example.com'
export EDGE_PLATFORM_ADMIN_PASSWORD='...'
export EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY='...'
```

## 4. Deploy

```bash
edge-installer validate installation.yaml
make up
```

## Commands

| Make | Installer | Action |
|------|-----------|--------|
| `make up` | `edge-installer apply` | Deploy all enabled services |
| `make plan` | `edge-installer plan` | Preview Terraform |
| `make status-aws` | `edge-installer status` | Status + health |
| `make down` | `edge-installer destroy --yes` | Tear down |
| — | `edge-installer upgrade` | Update app on existing host |

## What runs

```text
make up
  → Terraform (modules per enabled service)
  → SSH wait
  → Ansible site.yml
      → provision (Docker, dirs)
      → deploy (cloud_app, vpn, ...)
  → health check (cloud_app)
```

## Verify

```bash
curl http://<public-ip>/api/v1/health
open http://<public-ip>/
```

## More

- [Installer configuration](installer-configuration.md)
- [AWS deployment](aws-deployment.md)
- [EC2 smoke test](aws-ci.md) (manual GitHub Actions: Terraform + Ansible + destroy)
- [Troubleshooting](troubleshooting.md)

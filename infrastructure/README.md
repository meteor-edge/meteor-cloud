# Infrastructure

Terraform and Ansible for deploying modular services to AWS, plus a Terraform-only GCP Cloud Run stack.

## Layout

```text
infrastructure/
├── terraform/
│   ├── aws/                    # EC2 root stack — orchestrates modules
│   ├── gcp/                    # Cloud Run root stack
│   └── modules/
│       ├── cloud_app/          # EC2, security group, Elastic IP
│       ├── vpn/                # WireGuard UDP ingress rules
│       └── gcp_cloud_run/      # Cloud Run, Cloud SQL, Redis, load balancer
└── ansible/                    # AWS only
```

## Providers

| Provider | Path | How it runs |
|----------|------|-------------|
| AWS | `terraform/aws` + Ansible | EC2, Docker Compose, Traefik, optional VPN |
| GCP | `terraform/gcp` | Cloud Run, Cloud SQL, Memorystore, HTTP(S) LB |

See [GCP Cloud Run](../docs/gcp-deployment.md) and [AWS deployment](../docs/aws-deployment.md).

## AWS Ansible layout

```text
ansible/
    ├── ansible.cfg
    ├── playbooks/
    │   ├── site.yml            # Entry: provision + deploy
    │   ├── provision.yml       # Shared host setup (Docker, dirs)
    │   ├── deploy.yml          # Imports enabled service playbooks
    │   ├── upgrade.yml
    │   ├── destroy.yml
    │   └── services/
    │       ├── cloud_app.yml
    │       └── vpn.yml
    ├── roles/
    │   ├── common/
    │   ├── docker/
    │   ├── platform_*/
    │   └── vpn/
```

## Services

| Service | Terraform module | Ansible playbook | Description |
|---------|------------------|------------------|-------------|
| `cloud_app` | `modules/cloud_app` | `services/cloud_app.yml` | Edge Platform (Docker, Traefik, Postgres, Redis) |
| `vpn` | `modules/vpn` | `services/vpn.yml` | WireGuard VPN on the same EC2 host |

Enable or disable services in `installation.yaml` under `services:`. The installer passes `enabled_services` to Terraform and Ansible.

See [Modular services](../docs/services.md) for configuration and adding new services.

## How it is run

Normally you do **not** run Terraform or Ansible directly. Use:

```bash
make up      # edge-installer apply — Terraform + Ansible for enabled services
make down    # destroy
make plan    # preview
```

The installer copies Terraform modules into `.installer-state/<name>/terraform/` and runs playbooks with generated inventory and extra-vars.

## Manual use (debugging)

```bash
# Terraform (after installer has prepared workdir)
cd .installer-state/production/terraform
terraform plan -var-file=terraform.tfvars.json

# Ansible
cd infrastructure/ansible
ansible-playbook playbooks/site.yml \
  -i ../../.installer-state/production/inventory.ini \
  -e @../../.installer-state/production/extra-vars.json
```

## Validation

```bash
make terraform-check
make ansible-check
```

## Further reading

- [GCP Cloud Run](../docs/gcp-deployment.md)
- [AWS deployment](../docs/aws-deployment.md)
- [AWS prerequisites](../docs/aws-prerequisites.md)

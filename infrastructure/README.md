# Infrastructure

Provisioning for the **Docker Compose path on AWS**: Terraform creates the EC2
host, and `edge-installer` ties Terraform and Ansible together. Everything that
runs the application lives in [`deploy/`](../deploy/):

| Path | Purpose |
|------|---------|
| [`deploy/compose/`](../deploy/compose/) | Docker Compose files, `.env.example`, service config (EMQX, Traefik, observability) |
| [`deploy/ansible/`](../deploy/ansible/) | Server preparation and Compose deployment (any SSH host) |
| [`deploy/kubernetes/helm/meteorcloud/`](../deploy/kubernetes/helm/meteorcloud/) | Helm chart for existing clusters |

Terraform here provisions infrastructure only. It does not deploy the
application, and it does not create Kubernetes clusters: MeteorCloud does not
provision or manage Kubernetes clusters.

## Layout

```text
infrastructure/
├── installer/                  # edge-installer CLI (AWS: Terraform + Ansible)
└── terraform/
    └── aws/                    # EC2 root stack
        └── modules/
            ├── cloud_app/      # EC2, security group, Elastic IP
            └── vpn/            # WireGuard UDP ingress rule
```

## Services

| Service | Terraform module | Ansible playbook | Description |
|---------|------------------|------------------|-------------|
| `cloud_app` | `aws/modules/cloud_app` | `deploy/ansible/playbooks/services/cloud_app.yml` | MeteorCloud via Docker Compose (Traefik, API, console, PostgreSQL; optional Redis, MQTT) |
| `vpn` | `aws/modules/vpn` | `deploy/ansible/playbooks/services/vpn.yml` | WireGuard VPN on the same EC2 host |

Enable or disable services in `installation.yaml` under `services:`. The installer
passes `enabled_services` to Terraform and Ansible. See [Modular services](../docs/services.md).

## How it is run

```bash
make up      # edge-installer apply: Terraform + Ansible for enabled services
make plan    # preview
make down    # destroy
```

The installer copies `terraform/aws` (with its modules and lock file) into
`.installer-state/<name>/terraform/` and runs the playbooks with a generated
inventory and extra-vars.

## Manual use (debugging)

```bash
cd .installer-state/production/terraform
terraform plan -var-file=terraform.tfvars.json

cd deploy/ansible
ansible-playbook playbooks/site.yml \
  -i ../../.installer-state/production/inventory.ini \
  -e @../../.installer-state/production/extra-vars.json
```

## Validation (no cloud credentials needed)

```bash
make terraform-check   # fmt -check -recursive, init -backend=false, validate
make ansible-check ansible-lint
```

## Further reading

- [Deployment (Compose + Ansible)](../docs/deployment.md)
- [Kubernetes](../docs/kubernetes.md)
- [AWS deployment](../docs/aws-deployment.md)
- [AWS prerequisites](../docs/aws-prerequisites.md)

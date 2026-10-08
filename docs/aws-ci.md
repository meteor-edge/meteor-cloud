# EC2 smoke test (Terraform + Ansible)

Verifies that a fresh EC2 instance can be turned into a working MeteorCloud
install. It uses the EC2 path only: Terraform creates the host, Ansible deploys
the Docker Compose stack. Kubernetes installs use the Helm chart instead
([kubernetes.md](kubernetes.md)) and are smoke-tested on k3d in pull-request CI.

```text
Actions -> Run workflow
  -> tests, terraform fmt/validate, ansible syntax-check/lint
  -> edge-installer apply      Terraform: security group + EC2 (infrastructure/terraform/aws)
                               Ansible:   Docker + Compose stack (deploy/ansible, deploy/compose)
  -> scripts/smoke_test.py     /health, /api/v1/health, /
  -> edge-installer destroy    always, even when a step fails
```

Pull-request CI (`.github/workflows/ci.yml`) never touches AWS. This workflow
(`.github/workflows/aws-ci.yml`) is manual.

## Run it

GitHub -> Actions -> **EC2 smoke test** -> **Run workflow**, and pick the branch.

Expect 20-40 minutes: Docker images are built on the instance from the selected
commit.

## Secrets

Repository settings -> Secrets and variables -> Actions:

| Name | Required | Purpose |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | yes | IAM user key |
| `AWS_SECRET_ACCESS_KEY` | yes | IAM secret |
| `AWS_REGION` (variable) | no | Defaults to `eu-central-1` |

The PostgreSQL password, JWT secret, and SSH key pair are generated per run; the
key pair is deleted again at the end. The IAM user needs the same EC2 permissions
as a normal `make up` ([aws-prerequisites.md](aws-prerequisites.md)).

The repository must be cloneable from the EC2 host: the instance checks out
`https://github.com/<owner>/<repo>.git` at the workflow's commit.

## Same flow from your machine

The workflow is a thin wrapper around `edge-installer`, so you can run the
identical sequence locally with your own AWS credentials:

```bash
make install-installer
cp infrastructure/installer/edge_installer/config/examples/installation.yaml installation.yaml
$EDITOR installation.yaml          # region, SSH key, allowed SSH CIDRs, components
export EDGE_PLATFORM_POSTGRES_PASSWORD="$(openssl rand -hex 16)"
export EDGE_PLATFORM_JWT_SECRET="$(openssl rand -hex 32)"

make installer-validate            # config + local tools
make plan                          # terraform plan
make up                            # terraform apply, then the Ansible playbooks
python3 scripts/smoke_test.py "http://<public-ip>"
make down                          # ansible destroy, then terraform destroy
```

To run only one layer:

```bash
# Terraform alone (state lives in .installer-state/<name>/terraform after make up)
terraform -chdir=infrastructure/terraform/aws init -backend=false
terraform -chdir=infrastructure/terraform/aws validate

# Ansible alone, against any Ubuntu/Debian host (ansible-core 2.15+)
cd deploy/ansible
ansible-playbook -i inventory/<name>/hosts.yml playbooks/site.yml
```

## Cost and cleanup

Uses one `t3.small` for the length of the job. If the destroy step itself fails,
terminate leftover instances tagged `ManagedBy=edge-installer` with name
`ci-<run id>`, and delete the `ci-<run id>` key pair.

# AWS Terraform root stack

Creates the EC2 host for the Docker Compose deployment (used by `edge-installer`). It provisions infrastructure only; Ansible deploys the application.

## Modules

| Module | Path | When enabled |
|--------|------|--------------|
| `cloud_app` | `modules/cloud_app` | `cloud_app` in `enabled_services` |
| `vpn` | `modules/cloud_app` UDP rule (`same_host`) or `modules/vpn_gateway` (`dedicated`) | `vpn` in `enabled_services` |
| `dns` | `modules/dns` + `modules/dns_role` | `platform_domain` and `dns_zone_id` set |

## Variables

Key inputs (set by installer via `terraform.tfvars.json`):

- `enabled_services` — list, e.g. `["cloud_app", "vpn"]`
- `installation_name`, `aws_region`, `instance_type`, `ssh_key_name`, ...
- `vpn_listen_port`, `vpn_allowed_client_cidrs` — when VPN is enabled

## Outputs

- `instance_id`, `public_ip`, `elastic_ip`, `private_ip`
- `region`, `ssh_username`, `security_group_id`
- `enabled_services`, `vpn_listen_port`

## State

Managed locally by the installer:

```text
.installer-state/<installation>/terraform/
```

Do not commit state files.

## Local validation

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Or from repo root: `make terraform-check`.

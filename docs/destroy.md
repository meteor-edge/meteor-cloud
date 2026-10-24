# Destroy an installation

## This VM (`make up`)

On the host you installed with `make up`:

```bash
make down                      # stop; PostgreSQL and artifacts stay in /opt/edge-platform
make down ARGS=--destroy-data  # stop and delete /opt/edge-platform
```

`make down` does not delete AWS resources. Secrets remain in
`~/.meteorcloud/secrets.env` and `deploy/ansible/inventory/selfhost/secrets.env`
so a later `make up` can reuse them.

## AWS (`make aws-up`)

Removes AWS resources and local installer state for all enabled services.

```bash
edge-installer destroy installation.yaml          # prompts for confirmation
edge-installer destroy installation.yaml --yes

make aws-down                                     # same as destroy --yes
```

### What happens

1. Ansible `destroy.yml` (when SSH works):
   - Stops WireGuard if `vpn` was enabled
   - Stops Docker Compose stack if `cloud_app` was enabled
2. `terraform destroy` — EC2, SG, EIP, VPN rules
3. Deletes `.installer-state/<name>/installation.json`

### Warnings

- **All data on the EC2 instance is lost** (PostgreSQL, artifacts, VPN config)
- Elastic IP is released
- Cannot be undone

Confirm installation name and region before proceeding.

### Partial failure

If destroy fails:

1. Check SSH access to the instance
2. Inspect `.installer-state/<name>/terraform/terraform.tfstate`
3. Review resources in AWS console
4. Retry `edge-installer destroy installation.yaml --yes`

## Related

- [Install quickstart](install-quickstart.md)
- [AWS deployment](aws-deployment.md)
- [Troubleshooting](troubleshooting.md)

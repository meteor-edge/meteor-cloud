# Troubleshooting

## Validation failures

| Error | Fix |
|-------|-----|
| Missing `EDGE_PLATFORM_*` secrets | Export env vars (see [AWS prerequisites](aws-prerequisites.md)) |
| SSH key path missing | Create key pair; verify `aws.ssh_private_key_path` and `chmod 600` |
| Empty PEM file (`error in libcrypto`) | Recreate key pair — AWS only gives private key once |
| `vpn requires cloud_app` | Enable `services.cloud_app` or disable `services.vpn` |
| Tool not found | Install Terraform, Ansible, OpenSSH |

## Terraform errors

| Error | Fix |
|-------|-----|
| `UnauthorizedOperation` | Add EC2 permissions to IAM policy |
| `InvalidKeyPair.NotFound` | Create key pair in the **same region** as `aws.region` |
| `InsufficientInstanceCapacity` | Try another AZ or instance type |

## SSH errors

| Symptom | Fix |
|---------|-----|
| Connection timeout | Check `network.allowed_ssh_cidrs` includes your IP |
| `Permission denied (publickey)` | Key name on instance must match PEM file; recreate key if PEM lost |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | Elastic IP reused on new instance — run `ssh-keygen -R <ip>` or re-run apply (installer ignores stale keys) |
| Auth fails mid-apply | Wait for cloud-init; installer retries with backoff |

Manual test:

```bash
ssh -i ~/.ssh/edge-platform.pem ubuntu@<ip> 'echo ok'
```

## Ansible errors

| Symptom | Fix |
|---------|-----|
| `Could not load 'yaml' callback plugin` | Fixed in `ansible.cfg` — use `stdout_callback = default` |
| `repository_url is undefined` | Re-run apply so extra-vars include git settings; or use role defaults |
| Playbook fails mid-deploy | Fix issue, then `edge-installer apply installation.yaml` (idempotent) |

Manual run:

```bash
cd infrastructure/ansible
ansible-playbook playbooks/site.yml \
  -i ../../.installer-state/production/inventory.ini \
  -e @../../.installer-state/production/extra-vars.json
```

## PostgreSQL / Docker Compose unhealthy

Common cause: Compose interpolated empty `${POSTGRES_*}` from the host instead of `platform.env`.

Fix on server:

```bash
ssh -i ~/.ssh/edge-platform.pem ubuntu@<ip>
sudo docker compose -f /opt/edge-platform/docker-compose.yml down
sudo rm -rf /opt/edge-platform/data/postgres/*
sudo chown -R 70:70 /opt/edge-platform/data/postgres
exit
edge-installer apply installation.yaml
```

Check logs:

```bash
sudo docker compose -f /opt/edge-platform/docker-compose.yml logs postgres
sudo docker compose -f /opt/edge-platform/docker-compose.yml ps
```

## Platform health failures

```bash
ssh -i ~/.ssh/edge-platform.pem ubuntu@<ip>
sudo docker compose -f /opt/edge-platform/docker-compose.yml logs backend
sudo docker compose -f /opt/edge-platform/docker-compose.yml logs traefik
curl -s http://127.0.0.1/api/v1/health
```

Common causes:

- Images not built / wrong tag
- Git repo not pushed before deploy
- Database credential mismatch
- DNS not pointing to server (HTTPS)

## VPN

| Symptom | Fix |
|---------|-----|
| WireGuard not running | Set `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY` and re-deploy |
| Cannot connect | Check UDP port in SG (`services.vpn.listen_port`, default 51820) |
| No peer config | Add client peers to `/etc/wireguard/wg0.conf` manually (automation future work) |

## Security notes

This deployment is suitable for demos and early production. Not included: full OS hardening, WAF, centralized monitoring, automated backups.

Lock down `network.allowed_ssh_cidrs` to your IP in production.

## GCP Cloud Run

| Error | Fix |
|-------|-----|
| GCP credentials are not configured | `gcloud auth application-default login` or set `GOOGLE_APPLICATION_CREDENTIALS` |
| `services.vpn is not supported` | Set `services.vpn.enabled: false` |
| Cloud Run image not found | Push `deployment.backend_image` / `frontend_image` to Artifact Registry first |
| Health check timeout | Images must exist; first boot runs migrations. Increase `health_check_timeout_seconds` |
| Managed cert still provisioning | Point DNS at the load balancer IP; Google certs can take minutes |

See [GCP Cloud Run](gcp-deployment.md).

## Related

- [GCP Cloud Run](gcp-deployment.md)
- [Install quickstart](install-quickstart.md)
- [AWS deployment](aws-deployment.md)

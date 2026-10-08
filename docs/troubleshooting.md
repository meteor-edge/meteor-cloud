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
cd deploy/ansible
ansible-playbook playbooks/site.yml \
  -i ../../.installer-state/production/inventory.ini \
  -e @../../.installer-state/production/extra-vars.json
```

## Docker Compose services unhealthy

The stack lives in `/opt/edge-platform/compose` (Compose files plus the rendered
`.env`). Run Compose there with both files:

```bash
ssh -i ~/.ssh/edge-platform.pem ubuntu@<ip>
cd /opt/edge-platform/compose
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 100 backend postgres traefik
curl -s http://127.0.0.1/health
```

Ansible prints the last 150 log lines of every service when `docker compose up --wait` fails.

Common causes:

- `POSTGRES_PASSWORD` changed after the database was initialised. PostgreSQL keeps the
  first password; restore the old secret, or (losing all data) stop the stack and empty
  `/opt/edge-platform/data/postgres`.
- Images not built or wrong tag (`image_source: registry` needs pushed images).
- Git ref not pushed before deploy (`image_source: git`).
- DNS not pointing at the server, so Let's Encrypt cannot issue a certificate.

## Kubernetes

```bash
kubectl -n meteorcloud get pods,svc,ingress,pvc
kubectl -n meteorcloud logs deploy/meteorcloud-backend -c migrate   # migrations / first admin
kubectl -n meteorcloud logs deploy/meteorcloud-backend
helm -n meteorcloud test meteorcloud --logs
```

| Symptom | Fix |
|---------|-----|
| `helm install` fails with a values error | The chart rejects unsupported combinations; the message names the setting |
| Backend stuck in `Init` | The `migrate` init container waits for the database; check `DATABASE_URL` and network policies |
| Console loads but API calls fail | The Ingress must route `/api` to the backend; with `ingress.enabled=false` your own gateway must |
| Data plane cannot connect to EMQX | The broker certificate needs a SAN for `<release>-emqx` |
| `ImagePullBackOff` | Push images to a registry the cluster can reach and set `imagePullSecrets` |

## VPN

| Symptom | Fix |
|---------|-----|
| WireGuard not running | Set `EDGE_PLATFORM_VPN_SERVER_PRIVATE_KEY` and re-deploy |
| Cannot connect | Check UDP port in SG (`services.vpn.listen_port`, default 51820) |
| No peer config | Add client peers to `/etc/wireguard/wg0.conf` manually (automation future work) |

## Security notes

This deployment is suitable for demos and early production. Not included: full OS hardening, WAF, centralized monitoring, automated backups.

Lock down `network.allowed_ssh_cidrs` to your IP in production.

## Related

- [Deployment (Compose + Ansible)](deployment.md)
- [Kubernetes](kubernetes.md)
- [Install quickstart](install-quickstart.md)
- [AWS deployment](aws-deployment.md)

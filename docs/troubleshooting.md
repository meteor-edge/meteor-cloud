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
| Playbook fails mid-deploy | On this VM: re-run `make up`. AWS from a laptop: `make aws-up` / `edge-installer apply` (idempotent) |
| `edge-installer: not found` after `make up` | You are on the VM. Use `make up` (this host). `make aws-up` is only from a laptop that creates a new EC2. |
| `connectivity_internal_token` empty | Re-run `make up` on the host (it writes the token into extra-vars). |

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

- `POSTGRES_PASSWORD` changed after the database was initialised. The official
  image keeps the first role password in the volume. The next `make up` /
  `make aws-up` resets that role to the current secret (data stays). To wipe
  the database instead, stop the stack and empty `/opt/edge-platform/data/postgres`.
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
| No peer config | Run the gateway agent (`src/device-plane/gateway/connectivity_agent.py`) so it applies `wg set` from the control plane. Do not paste `[Peer]` blocks into `wg0.conf` |
| Logs window: "has not reported any log sources" | Re-run `sudo ./installcli.sh` (installs `meteorcli-logs` and its sudo rule), restart `meteorcli`; check `meteorcli logs` on the device |
| Device has no WG IP | `CONNECTIVITY_PROVIDER=wireguard`, `wireguard-tools` installed, and `meteorcli run` (passwordless sudo for the `meteorcli-wg` helper from `installcli.sh`; re-run `sudo ./installcli.sh` after upgrading) |
| Browser terminal fails | Device `sshd` up, `SHELL_PROVIDER=ssh_gateway`, gateway host can route to `10.8.0.0/16`, connectivity status `active` |
| Browser: `UNPROTECTED PRIVATE KEY` then Permission denied | Platform key must be `0600`. Re-run `make up`, or `sudo chmod 600 /opt/edge-platform/shell/id_ed25519` then Reconnect. Do not use `0640` or group `meteorcloud-shell` |
| Device card shows **Reconnecting** | The agent checks in but the tunnel is down. On the device: `sudo wg show` (handshake?), UDP 51820 reachable, `vpn.<domain>` resolves. The agent re-resolves the DNS name and restarts `wg0` on its own |
| `wireguard_key_changed` in the agent log | The device presented a new WireGuard key (reinstalled or cloned). Verify it, then **Re-enroll** on the device page |
| `Host key verification failed` / "host key changed" | The device's sshd key differs from the one pinned at enrollment. Check the device (reinstall?), then **Re-enroll** it to trust the new key |
| `device_offline` when opening a terminal | No WireGuard handshake in the last 3 minutes; see Reconnecting |
| `shell_certificate_required` | The shared gateway key is retired but this device does not trust the SSH CA yet. Update the agent and run `meteorcli run`; the device card should show **SSH auth: certificate** |
| `shell_certificate_failed` | The API could not sign a session certificate. Check that `SHELL_SSH_CA_FILE` exists in the API container, is `0600` and readable by the API user, and that `ssh-keygen` is installed |
| Device card shows **SSH auth: shared key** after an agent update | The agent installs the CA line in the SSH user's `~/.ssh/authorized_keys`; check that file (line ending in `meteorcloud-ca`) and the agent log |
| `meteorcloud-ssh: … is revoked` | Access was revoked; re-enroll the device to restore it |
| `meteorcloud-ssh` cannot read the key | Use `~/.config/meteorcloud/shell/id_ed25519` (`make up` copies it, mode `0600`). Do not chmod the platform key so the group can read it |

## Security notes

This deployment is suitable for demos and early production. Not included: full OS hardening, WAF, centralized monitoring, automated backups.

Lock down `network.allowed_ssh_cidrs` to your IP in production.

## Related

- [Deployment (Compose + Ansible)](deployment.md)
- [Kubernetes](kubernetes.md)
- [Install quickstart](install-quickstart.md)
- [AWS deployment](aws-deployment.md)

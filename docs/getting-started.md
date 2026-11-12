# Getting started

Run MeteorCloud on your **laptop** for testing and development, open the console, and enroll a Linux device with `meteorcli`.

| | Laptop (this page) | Production |
| --- | --- | --- |
| Command | `make dev` | `make up` on the VM |
| Stop | `make stop` (or `make down` with no selfhost inventory) | `make down` |
| Login | `owner@example.com` / `dev-password-123` (`make seed`) | printed at the end; `~/.meteorcloud/secrets.env` |
| Guide | this page · [Development](development.md) | [Install quickstart](install-quickstart.md) |

Do not run `make up` on a developer laptop. That path is Ansible + production Compose on a server.

**Prerequisites:** Docker Compose v2.24+, Make, and (for the agent) Python 3.13+ on the device or the same host.

## 1. Start the local stack

```bash
cp deploy/compose/.env.example deploy/compose/.env   # make dev does this if missing
make dev                 # add OBS=1 for Prometheus, Grafana and Loki
make seed      # owner@example.com / dev-password-123; demo fleet + good/fail releases
```

| Surface | URL |
| --- | --- |
| Console | http://localhost:5173 |
| Control plane API | http://localhost:8000 |
| OpenAPI | http://localhost:8000/docs |
| Data plane health | http://localhost:8081/health |
| MQTT TLS | mqtts://localhost:8883 |

Stop with `make stop`. More options: [Development](development.md).

The demo fleet contains three devices: one online, one offline, and one never seen. It also
includes Storefronts and Lab groups plus Raspberry Pi 5 and Intel NUC 13 device types.

`make seed` also creates **good** and **fail** releases you can Deploy from the console
(same workload per kind, so a fail Deploy replaces a good one):

| Release | Versions | Notes |
| --- | --- | --- |
| `seed-systemd` | `1.0.0-ok`, `1.0.0-fail` | systemd unit (`seed-systemd.service`) |
| `seed-docker` | `1.0.0-ok`, `1.0.0-fail` | container `seed-nginx` (`nginx:alpine` vs exiting `busybox`) |
| `seed-compose` | `1.0.0-ok`, `1.0.0-fail` | project `seed-compose` (nginx + HTML zip vs exiting busybox) |

If Deploy fails with `artifact_content_missing`, the release row exists but MinIO has no
file (host seed used AWS or Docker DNS). Re-run `make seed` with Compose MinIO up.

## 2. Sign in and create an organization

1. Open the console → sign in with the seed user (or register via `POST /api/v1/auth/register`).
2. Create an organization if seed did not create one for you.
3. Note the organization id from the UI or `GET /api/v1/organizations`.

## 3. Enroll a device

Pick one path.

### Path A — registration token (admin creates the token)

1. In the console: **Devices → Add device** (or `POST .../registration-tokens`).
2. On the device:

```bash
curl -fsSL https://github.com/meteor-edge/meteor-cloud/releases/latest/download/install-meteorcli.sh | sudo bash
# from a cloud checkout instead: cd src/device-plane/agent && sudo ./installcli.sh
sudo meteorcli register --server http://localhost:8000 --token reg_... --name edge-01
# meteorcli.service (started by the installer) connects within a few seconds:
journalctl -u meteorcli -f
# without root / systemd there is no service: meteorcli run   (foreground; Ctrl+C to stop)
```

Details: [Registration tokens](fleet/registration-tokens.md), [Device registration](fleet/device-registration.md).

### Path B — enrollment API key (device asks to join)

1. Create an enrollment API key in the console (or `POST .../enrollment-keys`). Copy the plaintext once.
2. On the device:

```bash
# install meteorcli first (same curl as Path A) if the device does not have it
sudo meteorcli config --domain localhost:8000 --api-key key_...
# from a Pi on the LAN to your laptop: --domain 192.168.0.118:8000
sudo meteorcli test
sudo meteorcli request-token --name edge-01
```

3. Approve the pending request in the console. `meteorcli.service` collects the
   device token and connects (heartbeats, MQTT, WireGuard); nothing else to run.
4. Without the service (non-root install, no systemd): `meteorcli request-token`
   waits up to ~5 minutes; after a later approval run `meteorcli claim`, then
   `meteorcli run`.

With meteorcli **0.3.2+** already installed, put `meteorenv.txt` on the boot
partition (`METEOR_DOMAIN`, `METEOR_API_KEY`, optional `METEOR_NAME`);
`meteorcli.service` submits the enrollment request and waits for approval.
For a full Pi image that also applies Wi-Fi / user / SSH on first boot, see
[first-boot enrollment](../src/device-plane/agent/packaging/firstboot/README.md).

Details: [Enrollment API keys](fleet/enrollment-api-keys.md), [Device-initiated enrollment](fleet/device-request-enrollment.md), [meteorcli](meteorcli.md).

## 4. What you should see

- Device appears in the console under the organization.
- Heartbeats update `last_seen_at` ([Heartbeat](fleet/heartbeat.md)).
- With MQTT enabled (`mqtt` / `emqx` Compose profiles), the agent keeps a TLS MQTT session ([MQTT](fleet/mqtt.md)).

## Next steps

| Goal | Doc |
| --- | --- |
| How the three planes fit together | [Concepts](concepts.md) |
| Operator API | [API overview](api.md) |
| Install on a VM (`make up`) | [Install quickstart](install-quickstart.md) · [Deployment](deployment.md) |
| Create a new AWS EC2 (`make aws-up`) | [AWS deployment](aws-deployment.md) |
| Kubernetes | [Kubernetes](kubernetes.md) |

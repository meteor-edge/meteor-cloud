# Raspberry Pi OS (device agent)

The supported device OS for native deploys right now is **Raspberry Pi OS**
(Debian Bookworm, 64-bit preferred). The agent (`meteorcli`) reports
`capabilities.systemd` / `docker` / `docker_compose` and applies releases on
heartbeat.

## Install on the Pi

On the Pi (Python 3.11+):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
# Copy or clone the agent tree, then:
cd src/device-plane/agent
sudo ./installcli.sh --with-service
```

Configure against your control plane (LAN IP or hostname):

```bash
sudo meteorcli config --domain 192.168.0.10:8000 --api-key key_...
# or HTTPS:
# sudo meteorcli config --domain cloud.example.com --api-key key_...
sudo meteorcli test
```

Enroll (registration token from **Devices → Add device**):

```bash
printf '%s' "reg_..." > /tmp/meteorcli.token
sudo meteorcli register --token-file /tmp/meteorcli.token --name pi-01
sudo systemctl start meteorcli
sudo systemctl status meteorcli
```

Or run once in the foreground while testing:

```bash
sudo meteorcli run --once
sudo meteorcli run
```

Root is required for **systemd** unit installs under `/etc/systemd/system`.
Docker/Compose also need the Docker engine (and usually membership in the
`docker` group, or root).

## Optional: Docker on the Pi

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker pi   # then re-login if you run without root
docker compose version
```

Heartbeats then report `docker: true` and `docker_compose: true` when the
Compose plugin is present.

## End-to-end test (systemd)

1. In the console: upload a `systemd` artifact (a `.service` unit).
2. **Releases → Create** (kind systemd, unit name matching the file).
3. **Deployments → Deploy** to the Pi.
4. Watch the device **Software** panel: `updating` → `in_sync` / `healthy`.
5. On the Pi: `systemctl status <unit_name>`.

Docker / Compose use the same Deploy wizard with the matching release kinds.

## Related

- [Agent README](../../src/device-plane/agent/README.md)
- [systemd deploys](systemd.md)
- [Deployments](deployments.md)
- [Heartbeat](heartbeat.md)

# Ansible (Docker Compose deployment)

Prepares a Linux server (VM, EC2, on-prem) and runs MeteorCloud on it with the
Docker Compose bundle in [`deploy/compose/`](../compose/). Ansible does not
template its own copy of the stack: it copies the repository's Compose files,
renders a `.env` file from inventory settings, and runs `docker compose`.

Ansible never deploys anything to Kubernetes. For clusters use the
[Helm chart](../kubernetes/helm/meteorcloud/).

Supported targets: Ubuntu 22.04/24.04 and Debian 12, reachable over SSH, with sudo.
Controller: ansible-core 2.15+ (`pip install ansible`; Debian 12's packaged 2.14 is too old).

## Playbooks

| Playbook | What it does |
|----------|--------------|
| `playbooks/site.yml` | `provision.yml` + `deploy.yml` (normal entry point) |
| `playbooks/provision.yml` | Packages, Docker Engine + Compose, log rotation, optional UFW firewall, directories |
| `playbooks/deploy.yml` | Imports the enabled service playbooks (`enabled_services`) |
| `playbooks/upgrade.yml` | Same as `deploy.yml`; set a new `git_ref` or image tags first |
| `playbooks/destroy.yml` | `docker compose down`; deletes `platform_root` only when `platform_destroy_data=true` |
| `playbooks/services/cloud_app.yml` | Compose bundle, `.env`, start, first admin, health check |
| `playbooks/services/vpn.yml` | WireGuard (only when `vpn` is in `enabled_services`) |

All playbooks are safe to re-run. A second run with unchanged settings reports no changes.

## Roles

| Role | Purpose |
|------|---------|
| `meteorcloud` | All settings and defaults (`defaults/main.yml`); listed first in every play |
| `common` | OS check, base packages, UTC timezone |
| `docker` | Docker Engine + Compose plugin from Docker's apt repo, json-file log rotation |
| `firewall` | UFW: SSH from allowed CIDRs, HTTP/HTTPS, MQTT 8883 when the broker is bundled |
| `platform_directories` | `/opt/edge-platform/` layout with the UIDs the containers need |
| `platform_config` | Copies `deploy/compose/`, renders `compose/.env` (mode 0600), MQTT certificates |
| `platform_deployment` | Builds (git) or pulls (registry) images, `docker compose up --wait`, first admin |
| `health_check` | `/health` and `/` through Traefik, from the host itself |
| `vpn` | WireGuard install and config |

## Configuration

Settings come from three places:

| Kind | Where |
|------|-------|
| Infrastructure choices (database, storage, MQTT, images, firewall) | `inventory/<name>/group_vars/platform.yml` |
| Secrets | `EDGE_PLATFORM_*` environment variables on the controller, or ansible-vault |
| Application defaults | `roles/meteorcloud/defaults/main.yml` |

Required secrets: `EDGE_PLATFORM_POSTGRES_PASSWORD` and `EDGE_PLATFORM_JWT_SECRET`.
Others are required only by the option that uses them, e.g.
`EDGE_PLATFORM_MQTT_INTERNAL_TOKEN` and `EDGE_PLATFORM_MQTT_PLATFORM_PASSWORD`
with MQTT, or `EDGE_PLATFORM_OBJECT_STORAGE_ACCESS_KEY_ID` and
`EDGE_PLATFORM_OBJECT_STORAGE_SECRET_ACCESS_KEY` with bundled MinIO (optional for
external S3 when the host has an IAM role). Every run checks these first and fails
with a clear message.

Infrastructure options:

| Setting | Values | Default |
|---------|--------|---------|
| `postgres_provider` | `bundled`, `external` (`EDGE_PLATFORM_DATABASE_URL`) | `bundled` |
| `cache_provider` | `memory`, `redis` (`redis_provider`: `bundled` / `external`) | `memory` |
| `object_storage_provider` | `filesystem`, `s3` (AWS S3, any S3-compatible API, GCS interoperability, or bundled MinIO with `minio_enabled`) | `filesystem` |
| `mqtt_enabled` / `mqtt_broker` | `false`, or `true` with `bundled` (EMQX) / `external` | `false` |
| `observability_enabled` | Prometheus, Loki, Alloy, Grafana on 127.0.0.1 | `false` |
| `image_source` | `git` (build on host), `registry` (pull) | `git` |

With the defaults the host runs only PostgreSQL, the API, the console, and Traefik.

## Run

```bash
cd deploy/ansible
cp -r inventory/example inventory/production   # ignored by git
$EDITOR inventory/production/hosts.yml inventory/production/group_vars/platform.yml

export EDGE_PLATFORM_POSTGRES_PASSWORD=... EDGE_PLATFORM_JWT_SECRET=...
ansible-galaxy collection install -r requirements.yml   # only with ansible-core
ansible-playbook -i inventory/production/hosts.yml playbooks/site.yml
```

`edge-installer` (AWS EC2) generates the inventory and extra-vars itself and runs
the same playbooks; see [AWS deployment](../../docs/aws-deployment.md).

## Upgrading from the single-file layout

Hosts deployed before `deploy/compose/` existed have `/opt/edge-platform/docker-compose.yml`.
The first run of the new playbooks stops that stack, removes the old generated
files, and starts the new bundle on the same data directories (`data/postgres`,
`traefik/acme.json`).

## Validation

```bash
make ansible-check   # syntax-check every playbook
make ansible-lint    # ansible-lint (production profile)
```

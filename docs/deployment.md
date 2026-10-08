# Deployment with Docker Compose and Ansible

For one Linux server: a VM, an EC2 instance, or an on-prem machine. For
Kubernetes clusters see [kubernetes.md](kubernetes.md).

The stack is defined once, in [`deploy/compose/`](../deploy/compose/). Ansible
prepares the server and runs those same files; it does not keep its own copy.

## What runs

| Service | When | Notes |
| --- | --- | --- |
| `traefik` | always (prod overlay) | Ports 80/443. Routes `/api` and `/health` to the API, the rest to the console. Let's Encrypt when `PLATFORM_DOMAIN` is set |
| `backend` | always | API; runs database migrations on start |
| `console` | always | Static SPA (nginx) |
| `postgres` | profile `postgres` | Omit and set `DATABASE_URL` for an external database |
| `redis` | profile `redis` | Only with `CACHE_PROVIDER=redis` |
| `minio` | profile `minio` | Only with `OBJECT_STORAGE_PROVIDER=s3` and no other S3 service |
| `data-plane` | profile `mqtt` | Needed whenever `MQTT_ENABLED=true` |
| `emqx` | profile `emqx` | Bundled broker on 8883 (TLS); omit to use an external EMQX |
| Prometheus, Loki, Alloy, Grafana | `docker-compose.observability.yml` | Bound to 127.0.0.1 |

`/internal/*` (broker callbacks) and `/metrics` are never routed by Traefik.

## Configuration

| Kind | Compose | Ansible |
| --- | --- | --- |
| Infrastructure choices | `COMPOSE_PROFILES`, `*_PROVIDER`, image names in `deploy/compose/.env` | `inventory/<name>/group_vars/platform.yml` |
| Secrets | `deploy/compose/.env` (mode 0600, never committed) | `EDGE_PLATFORM_*` env vars on the controller or ansible-vault; rendered into the host's `.env` |
| Application settings | `deploy/compose/.env` | `roles/meteorcloud/defaults/main.yml` |

Every key is documented in [`deploy/compose/.env.example`](../deploy/compose/.env.example).

### Storage options

| `OBJECT_STORAGE_PROVIDER` | Settings |
| --- | --- |
| `filesystem` | `OBJECT_STORAGE_PATH` inside the API container (`ARTIFACTS_DATA` volume on the host) |
| `s3`, AWS | `OBJECT_STORAGE_BUCKET`, `OBJECT_STORAGE_REGION`; keys optional with an instance role |
| `s3`, S3-compatible / customer | also `OBJECT_STORAGE_ENDPOINT_URL` and keys |
| `s3`, GCS | `OBJECT_STORAGE_ENDPOINT_URL=https://storage.googleapis.com` with HMAC keys (interoperability) |
| `s3`, bundled MinIO | profile `minio`, endpoint `http://minio:9000` |

Artifact downloads go through the API, so the storage endpoint never has to be public.

## Option 1: Ansible (recommended for servers)

Requirements: Ubuntu 22.04/24.04 or Debian 12 reachable over SSH with sudo; Ansible on your machine.

```bash
cd deploy/ansible
cp -r inventory/example inventory/production
$EDITOR inventory/production/hosts.yml inventory/production/group_vars/platform.yml
export EDGE_PLATFORM_POSTGRES_PASSWORD="$(openssl rand -hex 16)"
export EDGE_PLATFORM_JWT_SECRET="$(openssl rand -hex 32)"
ansible-playbook -i inventory/production/hosts.yml playbooks/site.yml
```

`site.yml` installs Docker, optionally configures UFW, creates `/opt/edge-platform`,
copies the Compose bundle, renders `.env`, starts the stack with
`docker compose up --wait`, and checks `/health`. It is safe to re-run; an unchanged
second run reports no changes. Details: [deploy/ansible/README.md](../deploy/ansible/README.md).

Upgrade: change `git_ref` (git builds) or image tags (registry), then run
`playbooks/upgrade.yml`. Destroy: `playbooks/destroy.yml` (add
`-e platform_destroy_data=true` to delete data).

On AWS, `edge-installer` creates the EC2 instance with Terraform and runs the same
playbooks: [aws-deployment.md](aws-deployment.md). To check that a fresh instance
still installs cleanly, run the manual **EC2 smoke test** workflow
([aws-ci.md](aws-ci.md)).

## Option 2: Docker Compose by hand

```bash
cd deploy/compose
cp .env.example .env
$EDITOR .env    # secrets, COMPOSE_PROFILES, providers, PLATFORM_DOMAIN, ACME_EMAIL
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --wait
```

Minimal production `.env` choices: `COMPOSE_PROFILES=postgres`,
`OBJECT_STORAGE_PROVIDER=filesystem`, `CACHE_PROVIDER=memory`, `MQTT_ENABLED=false`.

Verify any install: `python3 scripts/smoke_test.py https://fleet.example.com`.
`make compose-smoke` starts exactly that minimal stack on ports 18080/18443, runs
the smoke test, and removes it again.

## Backups

Back up PostgreSQL and the artifact store. Nothing else holds state:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > postgres.dump
```

Artifact files live in `/opt/edge-platform/data/artifacts` or your bucket.

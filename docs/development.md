# Development Guide

## Prerequisites

- Docker and Docker Compose v2.24+
- Optional, for Kubernetes: k3d, kubectl, helm
- Python 3.13+
- Node.js 22+
- Make

## First-time setup

```bash
cp deploy/compose/.env.example deploy/compose/.env

python -m venv .venv
source .venv/bin/activate

make install
```

`make install` installs control-plane, data-plane, installer, agent, and console dependencies. The public website is not in this tree; clone `meteor-ui` for that app.

## Running the stack

```bash
make dev                 # all Compose files (shared network meteorcloud)
make dev-control-plane   # bundled infra (postgres/redis/minio profiles) + control-plane API
make dev-data-plane      # EMQX + data-plane MQTT gateway
make dev-console         # operator console
```

Public website: clone `meteor-ui` and run `make dev-website` there.

Together this starts:

| Service | URL |
| --- | --- |
| Console | http://localhost:5173 |
| Control plane | http://localhost:8000 |
| Data plane health | http://localhost:8081/health |
| OpenAPI | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| MQTT TLS | mqtts://localhost:8883 |
| EMQX dashboard | http://localhost:18083 (development only) |
| Redis | internal Docker network |

Compose files:

| File | Services |
| --- | --- |
| `deploy/compose/control-plane.yml` | `backend`; profiles `postgres`, `redis`, `minio` |
| `deploy/compose/data-plane.yml` | profiles `mqtt` (`data-plane`), `emqx` |
| `deploy/compose/console.yml` | `console` (Vite dev server) |
| `deploy/compose/docker-compose.yml` | `include:` of those three |
| `deploy/compose/docker-compose.dev.yml` | Source mounts and hot reload (`make dev`) |
| `deploy/compose/docker-compose.prod.yml` | Production overlay: Traefik, restart policies, no internal ports |

`COMPOSE_PROFILES` in `deploy/compose/.env` selects the bundled services. Remove a
profile and point the matching setting (`DATABASE_URL`, `REDIS_URL`,
`OBJECT_STORAGE_ENDPOINT_URL`, `MQTT_BROKER_HOST`) at an external service instead.

Same machine stacks **must** use network name `meteorcloud`.

- Control plane alone: API works; MQTT/ping do not; console can still attach if `VITE_API_BASE_URL` points at that API.
- Data plane alone: broker listens; device connect fails until control plane auth is reachable.
- Console alone: static UI; unusable until a control plane is reachable at the configured API base URL.
- Together: `DATA_PLANE_URL` and `CONTROL_PLANE_URL` use Compose service names, not `localhost`.
- Console: `VITE_API_BASE_URL=http://localhost:8000` in the local browser. Never a data-plane host.
- Host-only (no Compose between them): `DATA_PLANE_URL=http://127.0.0.1:8081` and `CONTROL_PLANE_URL=http://127.0.0.1:8000`.

Telemetry is **PostgreSQL last-value** on `Device` (`TELEMETRY_PROVIDER=postgresql`). Timescale and ClickHouse are reserved names and fail fast.

Website content lives in `meteor-ui` under `website/content`. Marketing copy is JSON (`content/site.json`); docs are Markdown. Production uses `WEBSITE_CONTENT_SOURCE=s3` (JSON + `.md` + images on object storage), not control-plane Postgres. `/admin` edits that file set.

Stop with:

```bash
make stop
make stop-control-plane
make stop-data-plane
make stop-console
```

View logs:

```bash
make logs
```

## Running services without Docker (optional)

### Control plane

```bash
cd src/control-plane
export DATABASE_URL=postgresql+psycopg://edge:edge@localhost:5432/edge_platform
export DATA_PLANE_URL=http://127.0.0.1:8081
alembic upgrade head
uvicorn app.main:app --reload
```

### Data plane

```bash
cd src/data-plane
export CONTROL_PLANE_URL=http://127.0.0.1:8000
export MQTT_BROKER_HOST=127.0.0.1
uvicorn data_plane.main:app --host 0.0.0.0 --port 8081 --reload
```

### Console

```bash
cd console
npm run dev
```

The browser talks only to the control-plane API (`VITE_API_BASE_URL`).

### Website

Clone [`meteor-edge/meteor-ui`](https://github.com/meteor-edge/meteor-ui) and run `make dev-website` there. That app is not a directory in this repo.

### Installer

```bash
cd infrastructure/installer
edge-installer validate --config config/examples/installation.yaml
```

## Testing

```bash
make test
make backend-test
make data-plane-test
make console-test
```

Deployment checks (the same ones CI runs):

```bash
make compose-config    # docker compose config for base, dev, prod, observability
make compose-smoke     # minimal production stack + scripts/smoke_test.py (make images first)
make helm-lint         # helm lint + template for every values file
make terraform-check   # fmt, init -backend=false, validate (no credentials)
make ansible-check ansible-lint
./scripts/k8s-smoke.sh # throwaway k3d cluster: helm install, test, uninstall, delete
```

`make test` runs:

1. Installer Pytest suite
2. Control-plane Pytest suite (requires PostgreSQL; use `make dev` first)
3. Data-plane Pytest suite
4. Agent Pytest suite
5. Console Vitest suite (`make install-console` first)

Control-plane Pytest uses a sibling `*_test` database, never `DATABASE_URL` / `edge_platform`.

## Migrations and seed data

```bash
make migrate
make seed
```

See [Identity and organizations](identity-and-organizations.md) for auth and tenant details.

## Lint and format

```bash
make lint
make format
```

## Environment variables

Copy `deploy/compose/.env.example` to `deploy/compose/.env` and adjust as needed. Values are written from the containers' point of view (service names, not `localhost`). Important values:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection string for the running app |
| `TEST_DATABASE_URL` | Optional pytest database (must end in `_test`) |
| `JWT_SECRET_KEY` | Signing key for JWT utilities |
| `BACKEND_CORS_ORIGINS` | Allowed browser origins |
| `VITE_DOCS_BASE_URL` | Console → public docs site (`/docs`) |
| `SITE_URL` | Public website origin (canonical URLs, sitemap) |
| `WEBSITE_CONTENT_SOURCE` | Website content: `filesystem` (local JSON + Markdown) or `s3` (JSON file + docs + images on object storage) |
| `DATA_PLANE_URL` | Control plane → data-plane HTTP API |
| `CONTROL_PLANE_URL` | Data plane → control-plane ingest/auth origin |
| `TELEMETRY_PROVIDER` | Last-value store (`postgresql` only; timescale/clickhouse reserved) |
| `DATABASE_PROVIDER` | Persistence adapter (`postgresql` only) |
| `CACHE_PROVIDER` | Rate-limit adapter: `redis` (shared) or `memory` (per process) |
| `OBJECT_STORAGE_PROVIDER` | Artifacts: `s3` (any S3-compatible API) or `filesystem` (`OBJECT_STORAGE_PATH`) |
| `MQTT_PROVIDER` | MQTT adapter (`emqx` only) |
| `OTA_PROVIDER` | OTA adapter (`none` until a provider exists) |
| `IDENTITY_PROVIDER` | Member sign-in account directory (`local` only: PostgreSQL users) |

HTTP JSON between planes is documented in [`contracts/mqtt-http.md`](../contracts/mqtt-http.md).

## Adding a backend module later

1. Create `src/control-plane/app/<name>/`
2. Keep routers thin; put domain logic beside the module
3. Register routers from `app/main.py`
4. Add Alembic migrations for new tables
5. Add focused tests under `src/control-plane/tests/`
6. New infrastructure vendors implement a port in `app/ports/` — do not import vendor SDKs from domain services

## Adding an installer component later

1. Implement `PlatformComponent` in `infrastructure/installer/` as needed
2. Register it in the installer service registry
3. Wire enablement through configuration models

## Local Kubernetes

```bash
make k8s-up        # k3d cluster (development tool only)
make k8s-deploy    # build images, import, helm upgrade --install with values-dev.yaml
make k8s-status
make k8s-test
make k8s-down
```

The console and API are at http://localhost:8088, MQTT TLS at localhost:18883 (CA in
`.k8s/certs/ca.crt`). See [Kubernetes](kubernetes.md).

## Coding standards

- Type hints on all Python public functions
- Prefer small, explicit modules
- Raise `NotImplementedError` or print a friendly CLI message for unfinished work
- Avoid generic repository / CQRS / event-sourcing patterns unless a real product need appears

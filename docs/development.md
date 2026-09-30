# Development Guide

## Prerequisites

- Docker and Docker Compose
- Python 3.13+
- Node.js 22+
- Make

## First-time setup

```bash
cp .env.example .env

python -m venv .venv
source .venv/bin/activate

make checkout-ui     # private git@github.com:meteor-edge/meteor-ui.git
make install
```

`make checkout-ui` clones the private UI repo into `ui/` (git remote meteor-ui). `make install` installs control-plane, data-plane, installer, and agent dependencies, plus console when `ui/console` is present. The public website is not in this tree; clone `meteor-ui` (or `git -C ui sparse-checkout add website`) for that app.

## Running the stack

```bash
make dev                 # all Compose files (shared network meteorcloud)
make dev-control-plane   # postgres, redis, control-plane API
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
| `compose/control-plane.yml` | `postgres`, `redis`, `backend` |
| `compose/data-plane.yml` | `emqx`, `data-plane` |
| `compose/console.yml` | `console` |
| `docker-compose.yml` | `include:` of those three |

Same machine stacks **must** use network name `meteorcloud`.

- Control plane alone: API works; MQTT/ping do not; console can still attach if `VITE_API_BASE_URL` points at that API.
- Data plane alone: broker listens; device connect fails until control plane auth is reachable.
- Console alone: static UI; unusable until a control plane is reachable at the configured API base URL.
- Together: `DATA_PLANE_URL` and `CONTROL_PLANE_URL` use Compose service names, not `localhost`.
- Console: `VITE_API_BASE_URL=http://localhost:8000` in the local browser. Never a data-plane host.
- Host-only (no Compose between them): `DATA_PLANE_URL=http://127.0.0.1:8081` and `CONTROL_PLANE_URL=http://127.0.0.1:8000`.

Telemetry is **PostgreSQL last-value** on `Device` (`TELEMETRY_PROVIDER=postgresql`). Timescale and ClickHouse are reserved names and fail fast.

Website content lives in `meteor-ui` under `website/content` (`WEBSITE_CONTENT_SOURCE=filesystem`). A later CMS should use its own database, not the control-plane Postgres.

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

Runs:

1. Installer Pytest suite
2. Control-plane Pytest suite (requires PostgreSQL; use `make dev` first)
3. Data-plane Pytest suite
4. Agent Pytest suite
5. Console Vitest suite (after `make checkout-ui`)

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

Copy `.env.example` to `.env` and adjust as needed. Important values:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection string for the running app |
| `TEST_DATABASE_URL` | Optional pytest database (must end in `_test`) |
| `JWT_SECRET_KEY` | Signing key for JWT utilities |
| `BACKEND_CORS_ORIGINS` | Allowed browser origins |
| `VITE_DOCS_BASE_URL` | Console → public docs site (`/docs`) |
| `SITE_URL` | Public website origin (canonical URLs, sitemap) |
| `WEBSITE_CONTENT_SOURCE` | `filesystem` (implemented) or `database` (reserved, website-owned DB) |
| `DATA_PLANE_URL` | Control plane → data-plane HTTP API |
| `CONTROL_PLANE_URL` | Data plane → control-plane ingest/auth origin |
| `TELEMETRY_PROVIDER` | Last-value store (`postgresql` only; timescale/clickhouse reserved) |
| `DATABASE_PROVIDER` | Persistence adapter (`postgresql` only) |
| `CACHE_PROVIDER` | Rate-limit adapter (`redis` only) |
| `MQTT_PROVIDER` | MQTT adapter (`emqx` only) |
| `OTA_PROVIDER` | OTA adapter (`none` until a provider exists) |

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

## Kubernetes later

Do not add manifests in this repo yet. A later split would be Deployments:
`control-plane`, `data-plane`, and `console`. Scale the API and console independently. The public website is a separate deploy from `meteor-ui`.
Keep the data-plane MQTT consumer at 1 until shared subscriptions / a consumer group exist.
`infrastructure/kubernetes` stays empty.

## Coding standards

- Type hints on all Python public functions
- Prefer small, explicit modules
- Raise `NotImplementedError` or print a friendly CLI message for unfinished work
- Avoid generic repository / CQRS / event-sourcing patterns unless a real product need appears

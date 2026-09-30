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

make install
```

`make install` installs backend, frontend, and installer dependencies.

## Running the stack

```bash
make dev
```

This starts:

| Service | URL |
| --- | --- |
| Frontend | http://localhost:5173 |
| Backend | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| MQTT TLS | mqtts://localhost:8883 |
| EMQX dashboard | http://localhost:18083 (development only) |
| Redis | internal Docker network |

Stop with:

```bash
make stop
```

View logs:

```bash
make logs
```

## Running services without Docker (optional)

### Backend

```bash
# Start PostgreSQL somehow, then:
cd control-plane
export PYTHONPATH=.:../data-plane
export DATABASE_URL=postgresql+psycopg://edge:edge@localhost:5432/edge_platform
alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm run dev
```

### Installer

```bash
cd infrastructure/installer
edge-installer validate --config config/examples/installation.yaml
```

## Testing

```bash
make test
make backend-test
make frontend-test
```

Runs:

1. Installer Pytest suite
2. Backend Pytest suite (requires PostgreSQL; use `make dev` first)
3. Frontend Vitest suite

Backend tests **never** use `DATABASE_URL` / `edge_platform`. They rewrite the
connection to a sibling `edge_platform_test` database and refuse to start if
that rewrite would target a non-local host. `make dev` keeps using
`edge_platform`, so registering a device and then running tests will not wipe
your development data.

## AWS CI

To run tests, launch a throwaway EC2 install, smoke-check it, and destroy it,
store `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` as GitHub Actions secrets
and run the **AWS CI** workflow. See [AWS CI](aws-ci.md).

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
| `VITE_API_BASE_URL` | Frontend → backend base URL |
| `DATABASE_PROVIDER` | Persistence adapter (`postgresql` only) |
| `CACHE_PROVIDER` | Rate-limit adapter (`redis` only) |
| `MQTT_PROVIDER` | MQTT adapter (`emqx` only) |
| `OTA_PROVIDER` | OTA adapter (`none` until a provider exists) |

## Adding a backend module later

1. Create `control-plane/app/<name>/`
2. Keep routers thin; put domain logic beside the module
3. Register routers from `app/main.py`
4. Add Alembic migrations for new tables
5. Add focused tests under `control-plane/tests/`
6. New infrastructure vendors implement a port in `app/ports/` — do not import vendor SDKs from domain services

## Adding an installer component later

1. Implement `PlatformComponent` in `infrastructure/installer/` as needed
2. Register it in the installer service registry
3. Wire enablement through configuration models

## Coding standards

- Type hints on all Python public functions
- Prefer small, explicit modules
- Raise `NotImplementedError` or print a friendly CLI message for unfinished work
- Avoid generic repository / CQRS / event-sourcing patterns unless a later
  milestone truly needs them

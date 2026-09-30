# Edge Platform control plane

FastAPI control-plane API for MeteorCloud: identity, organizations, device
registry, enrollment, MQTT policy/ingest, and operator APIs.

## Stack

- Python 3.13
- FastAPI
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Pydantic v2

## Local development

```bash
# Prefer Make from the repository root:
make install-backend
make dev

# Or run the API directly:
cd control-plane
export DATA_PLANE_URL=http://127.0.0.1:8081
uvicorn app.main:app --reload
```

MQTT publish/watch goes to the data-plane HTTP API. EMQX still authenticates
against this process at `/internal/mqtt/authenticate` and `/authorize`.

## Health

```bash
curl http://localhost:8000/health
```

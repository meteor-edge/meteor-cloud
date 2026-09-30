# Edge Platform Backend

FastAPI control-plane API for the Edge Platform.

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
export PYTHONPATH=.:../data-plane
uvicorn app.main:app --reload
```

## Health

```bash
curl http://localhost:8000/health
```

## Milestone 1 scope

Configuration, database, logging, health endpoint, JWT/password utilities, and
authentication dependencies. No business modules yet.

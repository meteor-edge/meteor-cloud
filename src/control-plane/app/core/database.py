"""Database engine, session factory, and FastAPI dependency.

PostgreSQL is the current store (SQLAlchemy + psycopg). Domain code uses
repository classes, not this module's engine, except at composition roots.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
    pool_recycle=settings.db_pool_recycle_seconds,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session]:
    """Yield a database session and ensure it is closed afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

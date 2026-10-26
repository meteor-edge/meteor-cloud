"""FastAPI dependencies for connectivity APIs."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters import connectivity_provider
from app.adapters import rate_limiter as build_rate_limiter
from app.connectivity.service import ConnectivityService
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.devices.rate_limit import RateLimiter


def get_connectivity_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectivityService:
    return ConnectivityService(
        session,
        provider=connectivity_provider(settings),
        settings=settings,
    )


@lru_cache
def _build_connectivity_rate_limiter() -> RateLimiter:
    # Built once: an in-memory limiter created per request would never count past one.
    settings = get_settings()
    return build_rate_limiter(
        settings,
        limit=settings.connectivity_rate_limit_requests,
        window_seconds=settings.connectivity_rate_limit_window_seconds,
        prefix="conn_rl",
    )


def get_connectivity_rate_limiter() -> RateLimiter:
    return _build_connectivity_rate_limiter()


ConnectivitySvc = Annotated[ConnectivityService, Depends(get_connectivity_service)]

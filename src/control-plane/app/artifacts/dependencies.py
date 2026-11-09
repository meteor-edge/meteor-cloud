"""FastAPI dependencies for the artifact API."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.adapters import object_storage as build_object_storage
from app.adapters import rate_limiter as build_redis_rate_limiter
from app.artifacts.service import ArtifactService
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.metrics import RATE_LIMITED
from app.devices.rate_limit import RateLimiter
from app.ports.storage import ObjectStorage


@lru_cache
def _build_object_storage() -> ObjectStorage:
    """Build and cache the configured storage adapter; propagate setup errors."""
    return build_object_storage(get_settings())


def get_object_storage() -> ObjectStorage:
    """Return the cached storage adapter; propagate adapter setup errors."""
    return _build_object_storage()


def get_artifact_service(
    session: Annotated[Session, Depends(get_db)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArtifactService:
    """Bind artifact operations to the request's session, storage, and settings."""
    return ArtifactService(session, storage, settings=settings)


ArtifactSvc = Annotated[ArtifactService, Depends(get_artifact_service)]


@lru_cache
def _build_public_os_image_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return build_redis_rate_limiter(
        settings,
        limit=settings.public_os_image_rate_limit_requests,
        window_seconds=settings.public_os_image_rate_limit_window_seconds,
        prefix="public_os_image_rl",
    )


def get_public_os_image_rate_limiter() -> RateLimiter:
    return _build_public_os_image_rate_limiter()


def enforce_public_os_image_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_public_os_image_rate_limiter)],
) -> None:
    """These routes need no login, so cap each source IP."""
    client_host = request.client.host if request.client else "unknown"
    if not limiter.allow(client_host):
        RATE_LIMITED.labels(limiter="public_os_image").inc()
        raise AppError("rate_limited", "Too many requests. Please try again later.", status_code=429)

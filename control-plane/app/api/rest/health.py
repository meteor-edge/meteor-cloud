"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Health check payload."""

    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return service health for load balancers and local checks."""
    return HealthResponse(status="ok", service="edge-platform-backend", version="0.1.0")


@router.get("/api/v1/health", response_model=HealthResponse)
def health_v1() -> HealthResponse:
    """Versioned health endpoint for reverse proxies and installers."""
    return health()
